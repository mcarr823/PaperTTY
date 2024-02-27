from usb_it8951 import IT8951UsbDevice
from PIL import Image, ImageDraw, ImageFont
import datetime
import time

def clear(device):
    w = 1872
    h = 1404
    image = Image.new('1', (w,h), "#FFFFFF")
    device.draw(image, 0, 0, display_mode=device.INIT)
    time.sleep(0.1)
    wait_for_display_ready(device)

def wait_for_display_ready(device):
    expected = bytearray([0,0])
    while device.read_register(device.REG_LUTAFSR, 2) != expected:
        print("Result was: "+str(list(result)))
        time.sleep(0.1)

device = IT8951UsbDevice(0x048d, 0x8951)
clear(device)

bpp = 1
device.set_bpp(bpp)

step_height = 20
for i in range(0, 70):
    image = Image.new('1', (1872,step_height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    path = '/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf'
    font = ImageFont.truetype(path, 12)
    fill = "#000000"
    line = "Time is:"+str(datetime.datetime.now())
    draw.text((0, 0), line, font=font, fill=fill, spacing=1)

    #flipx
    image = image.transpose(Image.FLIP_LEFT_RIGHT)

    w = image.width
    h = image.height
    print(f"Image {w}x{h}")

    device.draw(image, 0, i * step_height)

device.close()