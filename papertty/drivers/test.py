from usb_it8951 import IT8951UsbDevice
from PIL import Image, ImageDraw, ImageFont
import datetime
import time

REG_DISPLAY_BASE = 0x1000
REG_LUTAFSR = REG_DISPLAY_BASE + 0x224 # LUT Status Reg (status of All LUT Engines)
REG_UP1SR = REG_DISPLAY_BASE + 0x138 #Update Parameter1 Setting Reg
REG_BGVR = REG_DISPLAY_BASE + 0x250 #Bitmap (1bpp) image color table
REG_WIDTH = REG_DISPLAY_BASE + 0x24C

REG_SYSTEM_BASE = 0
REG_I80CPCR = REG_SYSTEM_BASE + 0x04

USB_ADJUST = 0x18000000

Back_Gray_Val = 0xF0
Front_Gray_Val = 0x00

device = IT8951UsbDevice(0x048d, 0x8951)

sys_info = device.get_system_info()
address = sys_info[7]

# (vendor, product, revision) = device.inquiry()
# print("vendor: "+bytes(vendor).decode("utf-8"))
# print("product: "+bytes(product).decode("utf-8"))
# print("revision: "+bytes(revision).decode("utf-8"))

INIT = 0
A2 = 6
DU4 = 1






def pack_image(image, bpp, pitch):
    """Packs a PIL image for transfer over SPI to the driver board."""

    if bpp == 1:
        # B/W pictured can be processed more quickly
        frame_buffer = list(image.convert('1').getdata())
    else:
        return bytearray(image.convert('L').tobytes('raw'))

    step = 8

    bpr = int(image.width / step)
    rem = image.width % 32
    if rem > 0:
        bpr += 1

    #rows = chunks(frame_buffer, image.width)
    packed_buffer = [0x00] * (bpr * 4 * image.height)

    pb_index = 0
    fb_index = 0
    for y in range(0, image.height):
        for x in range(0, image.width, step):
            fb = frame_buffer[fb_index:fb_index+step]
            pack_1bpp(packed_buffer, pb_index // step, fb)
            pb_index += step
            fb_index += step
        if rem > 0:
            for x in range(0, rem, step):
                packed_buffer[pb_index // step] = 0
                pb_index += step

    return packed_buffer

def pack_1bpp(packed_buffer, i, eightBytes):
    packed_buffer[i] = \
        (1 if eightBytes[0] else 0) | \
        (2 if eightBytes[1] else 0) | \
        (4 if eightBytes[2] else 0) | \
        (8 if eightBytes[3] else 0) | \
        (16 if eightBytes[4] else 0) | \
        (32 if eightBytes[5] else 0) | \
        (64 if eightBytes[6] else 0) | \
        (128 if eightBytes[7] else 0)

def clear(pitch):
    w = 1872
    h = 1404
    image = Image.new('1', (w,h), "#FFFFFF")
    ydiv = 64
    #ydiv = int(device.MAX_TRANSFER / pitch)
    for y in range(0, h, ydiv):
        remaining = h - y
        chunk_height = min(ydiv, remaining)
        bbox = (0,y,w,y+chunk_height)
        cropped = image.crop(bbox).convert('1')
        packed = pack_image(cropped, 1, pitch)
        device.load_image_area(x = bbox[0], y = bbox[1], w = cropped.width, h = cropped.height, buffer = packed, pitch = pitch)
        # print("Bytes to write: "+str(len(packed)))
    device.display_area(x = 0, y = 0, w = image.width, h = image.height, display_mode = INIT)
    time.sleep(0.1)
    wait_for_display_ready()

def wait_for_display_ready():
    expected = bytearray([0,0])
    while device.read_register(REG_LUTAFSR, 2) != expected:
        print("Result was: "+str(list(result)))
        time.sleep(0.1)

table_version = sys_info[3].to_bytes(4, 'little')
panel_width = sys_info[4]
panel_height = sys_info[5]

bpp = 1

device_width = ((panel_width + 31) // 32)
mem_pitch_1bpp = (((panel_width + 31) // 32) * 4)
mem_pitch_8bpp = panel_width
if bpp == 1:
    pitch = mem_pitch_1bpp
else:
    pitch = mem_pitch_8bpp

# print(f"Pitch {pitch}")
# print(f"Table version {table_version}")

cur_val = device.read_register(REG_UP1SR, 4)
cur_val[1] = 0
cur_val[2] |= 0x06
device.write_register(REG_UP1SR, cur_val)
clear(mem_pitch_1bpp)

image = Image.new('1', (1872,1404), "#FFFFFF")
draw = ImageDraw.Draw(image)
path = '/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf'
font = ImageFont.truetype(path, 48)
fill = "#000000"
line = "Time is:"+str(datetime.datetime.now())
draw.text((0, 0), line, font=font, fill=fill, spacing=1)
#flipx
image = image.transpose(Image.FLIP_LEFT_RIGHT)

#packed = list(image.getdata())
#packed = pack_image(image, bpp, pitch)

w = image.width
h = image.height
print(f"Image {w}x{h}")


# 1bpp mode
cur_val = device.read_register(REG_UP1SR, 4)
# print("Old val: "+str(cur_val))
#cur_val[2] = 0

if bpp == 1:
    cur_val[2] |= 0x06
elif bpp == 8:
    cur_val[2] &= 0xf9

device.write_register(REG_UP1SR, cur_val)

new_val = device.read_register(REG_UP1SR, 4)

# print("New val: "+str(new_val))

# 1bpp color table
color_bytes = device.int_to_bytes((Front_Gray_Val<<8) | Back_Gray_Val, big=True)
device.write_register(REG_BGVR, [0xf0, 0x00])

width_as_bytes = list(device_width.to_bytes(2, 'little'))
device.write_register(REG_WIDTH, width_as_bytes)

if bpp == 1:
    display_mode = A2
else:
    display_mode = DU4

ydiv = int(device.MAX_TRANSFER / (pitch * 8 / bpp))
written = 0
for y in range(0, h, ydiv):
    remaining = h - y
    chunk_height = min(ydiv, remaining)
    bbox = (0,y,w,y+chunk_height)
    # print("bbox: "+str(bbox))
    cropped = image.crop(bbox)
    packed = pack_image(cropped, bpp, pitch)
    device.load_image_area(x = bbox[0], y = bbox[1], w = cropped.width, h = cropped.height, buffer = packed, pitch = pitch)
    written += len(packed)

print(f"written: {written}")

print(f"refresh: 0x0x{w}x{h}")
device.display_area(x = 0, y = 0, w = w, h = h, display_mode = display_mode)

device.close()