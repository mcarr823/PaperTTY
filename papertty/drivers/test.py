from usb_it8951 import IT8951UsbDevice
from PIL import Image, ImageDraw, ImageFont
import datetime

REG_DISPLAY_BASE = 0x1000
REG_LUTAFSR = REG_DISPLAY_BASE + 0x224 # LUT Status Reg (status of All LUT Engines)
REG_UP1SR = REG_DISPLAY_BASE + 0x138 #Update Parameter1 Setting Reg
REG_BGVR = REG_DISPLAY_BASE + 0x250 #Bitmap (1bpp) image color table

REG_SYSTEM_BASE = 0
REG_I80CPCR = REG_SYSTEM_BASE + 0x04

USB_ADJUST = 0x18000000

Back_Gray_Val = 0xF0
Front_Gray_Val = 0x00

device = IT8951UsbDevice(0x048d, 0x8951)

sys_info = device.get_system_info()

# (vendor, product, revision) = device.inquiry()
# print("vendor: "+bytes(vendor).decode("utf-8"))
# print("product: "+bytes(product).decode("utf-8"))
# print("revision: "+bytes(revision).decode("utf-8"))








def pack_image(image, bpp):
    """Packs a PIL image for transfer over SPI to the driver board."""
    if image.mode == '1':
        # B/W pictured can be processed more quickly
        frame_buffer = list(image.getdata())
    else:
        # old packing code for grayscale (VNC)
        bpp = 4
        image_grey = image.convert("L")
        frame_buffer = list(image_grey.getdata())


    #Step is the number of bytes we need to read to create a word.
    #A word is 2 bytes (16 bits) in size.
    #However, the input data we use to create the word will vary
    #in length depending on the bpp.
    #eg. If bpp is 1, that means we only grab 1 bit from each
    #input byte. So we would need 16 bytes to get the needed
    #16 bits.
    #Whereas if bpp is 4, then we grab 4 bits from each byte.
    #So we'd only need to read 4 bytes to get 16 bits.
    step = 16 // bpp

    #A halfstep is how many input bytes we need to read from
    #frame_buffer in order to pack a single output byte
    #into packed_buffer.
    halfstep = step // 2

    #Set the size of packed_buffer to be the length of the
    #frame buffer (total input bytes) divided by a halfstep
    #(input bytes needed per packed byte).
    packed_buffer = [0x00] * (len(frame_buffer) // halfstep)

    #Select the packing function based on which bpp
    #mode we're using.
    if bpp == 1:
        packfn = pack_1bpp
    else:
        packfn = pack_8bpp

    #Step through the frame buffer and pack its bytes
    #into packed_buffer.
    for i in range(0, len(frame_buffer), step):
        packfn(packed_buffer, i // halfstep, frame_buffer[i:i+step])
    return packed_buffer

def pack_1bpp(packed_buffer, i, sixteenBytes):
    """Pack an image in 1bpp format.

    This only works for black and white images.
    This code would look nicer with a loop, but using bitwise operators
    like this is significantly faster. So the ugly code stays ;)

    Bytes are read in reverse order because the driver board assumes all
    data is read in as 16bit ints. So in order to match the endianness,
    every pair of bytes must be swapped.
    """
    packed_buffer[i] = \
        (1 if sixteenBytes[8] else 0) | \
        (2 if sixteenBytes[9] else 0) | \
        (4 if sixteenBytes[10] else 0) | \
        (8 if sixteenBytes[11] else 0) | \
        (16 if sixteenBytes[12] else 0) | \
        (32 if sixteenBytes[13] else 0) | \
        (64 if sixteenBytes[14] else 0) | \
        (128 if sixteenBytes[15] else 0)
    packed_buffer[i+1] = \
        (1 if sixteenBytes[0] else 0) | \
        (2 if sixteenBytes[1] else 0) | \
        (4 if sixteenBytes[2] else 0) | \
        (8 if sixteenBytes[3] else 0) | \
        (16 if sixteenBytes[4] else 0) | \
        (32 if sixteenBytes[5] else 0) | \
        (64 if sixteenBytes[6] else 0) | \
        (128 if sixteenBytes[7] else 0)

def pack_8bpp(packed_buffer, i, twoBytes):
    """Pack an image in 8bpp format.

    Bytes are read in reverse order because the driver board assumes all
    data is read in as 16bit ints. So in order to match the endianness,
    every pair of bytes must be swapped.
    """
    packed_buffer[i] = twoBytes[1]
    packed_buffer[i+1] = twoBytes[0]


panel_width = sys_info[4]
image = Image.new('1', (600,60), "#FFFFFF")
draw = ImageDraw.Draw(image)
path = '/usr/share/fonts/truetype/ubuntu/Ubuntu-L.ttf'
font = ImageFont.truetype(path, 48)
fill = "#000000"
line = "Test:"+str(datetime.datetime.now())
draw.text((0, 0), line, font=font, fill=fill, spacing=1)
#flipx
image = image.transpose(Image.FLIP_LEFT_RIGHT)


bpp = 8

packed = pack_image(image, bpp)

w = image.width
h = image.height


# 1bpp mode
cur_val = device.read_register(REG_UP1SR, 4)

if bpp == 1:
    cur_val[2] |= 0x06
elif bpp == 8:
    cur_val[2] &= 0xf9

device.write_register(REG_UP1SR, cur_val)

# 1bpp color table
color_bytes = device.int_to_bytes((Front_Gray_Val<<8) | Back_Gray_Val, big=True)
device.write_register(REG_BGVR, [0xf0, 0x00])

device_width = ((panel_width + 31) // 32) * 4
#device_width = device_width // 8
width_as_bytes = list(device_width.to_bytes(4, 'little'))
# device.write_register(0x1800_124c,
#     [ width_as_bytes[0], width_as_bytes[1] ],
# )

# Set to Enable I80 Packed mode.
#device.write_register(REG_I80CPCR, [0x00, 0x01])

device.load_image_area(x = 0, y = 0, w = w, h = h, buffer = packed)
device.display_area(x = 0, y = 0, w = w, h = h, display_mode = 6)

device.close()