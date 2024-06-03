# This code has partially been adapted from https://github.com/faassen/rust-it8951
# and https://github.com/blahgeek/rabbitink
# Per https://www.waveshare.com/w/upload/c/c9/IT8951_USB_ProgrammingGuide_v.0.4_20161114.pdf


try:
    from usb.core import find as find_usb
    from usb.util import claim_interface as claim_usb
except Exception as e:
    pass

class IT8951UsbDevice():

    # ENDPOINT_IN = 0x01
    # ENDPOINT_OUT = 0x02
    ENDPOINT_IN = 0
    ENDPOINT_OUT = 1

    # Display modes
    INIT = 0
    A2 = 6
    DU4 = 1

    # maximum transfer size is 60k bytes for IT8951 USB
    # 20 bytes are used for the headers of an area load request,
    # so subtract that from 60k to give the max data chunk size
    MAX_TRANSFER = (60 * 1024) - 20

    INQUIRY_CMD = [18, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    # 254 = 0xFE = "Customer Command"
    GET_SYS_CMD = [254, 0, 56, 57, 53, 49, 128, 0, 1, 0, 2, 0, 0, 0, 0, 0]
    LD_IMAGE_AREA_CMD = [254, 0, 0, 0, 0, 0, 162, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    DPY_AREA_CMD = [254, 0, 0, 0, 0, 0, 148, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    REG_DISPLAY_BASE = 0x1000
    REG_LUTAFSR = REG_DISPLAY_BASE + 0x224 # LUT Status Reg (status of All LUT Engines)
    REG_UP1SR = REG_DISPLAY_BASE + 0x138 #Update Parameter1 Setting Reg
    REG_BGVR = REG_DISPLAY_BASE + 0x250 #Bitmap (1bpp) image color table
    REG_WIDTH = REG_DISPLAY_BASE + 0x24C

    REG_SYSTEM_BASE = 0
    REG_I80CPCR = REG_SYSTEM_BASE + 0x04

    REG_ADJUST = 0x18000000

    def __init__(self, vid, pid):
        (self.endpoint_in, self.endpoint_out, self.device) = self.find_usb_device(vid, pid)
        self.tag_num = 0

        sys_info = self.get_system_info()
        self.img_addr = sys_info[7]
        self.panel_width = sys_info[4]

        self.set_bpp(1)

    def close(self):
        self.device.reset()

    def get_tag(self):
        self.tag_num += 1
        return self.tag_num

    def find_usb_device(self, vid, pid):
        device = find_usb(idVendor=vid, idProduct=pid)
        
        if device is None:
            raise ValueError("USB device not found.")

        device.reset()

        if device.is_kernel_driver_active(0):
            device.detach_kernel_driver(0)

        device.set_configuration()
        interface_number = 0
        claim_usb(device, interface_number)
        endpoints = device[0][(0, 0)]
        
        return [
            endpoints[self.ENDPOINT_IN],
            endpoints[self.ENDPOINT_OUT],
            device
        ]

    def read(self, length):
        # 10 second timeout.
        # This is overkill, but the default is way too short
        timeout = 10000
        return self.endpoint_in.read(length, timeout)

    def write(self, byte_list):
        return self.endpoint_out.write(bytes(byte_list))

    """
        command: 16 bytes
    """
    def read_command(self, command, length, big):
        
        # issue CBW block
        cbw_data = self.get_command_block_wrapper(command, length, True, big = big)
        written = self.write(cbw_data)

        # print("Command "+str(command))
        # print("Length "+str(length))
        # print("CBW: "+str(cbw_data))
        # print("Written "+str(written))

        # now read the data
        buf = self.read(length)
        # print("Received "+str(buf))

        # issue CBS block
        self.send_status_block_wrapper()

        # transform data into required data
        return buf

    """
        command: 16 bytes
        value_data: byte array
        data: byte array
    """
    def write_command(self, command, value_data, extra_data = [], big = False):

        # combine this with any additional data
        bulk_data = [ *value_data, *extra_data ]

        # issue CBW block
        cbw_data = self.get_command_block_wrapper(command, len(bulk_data), False, big = big)
        self.write(cbw_data)

        # now write the data for the value
        self.write(bulk_data)

        # issue CBS block
        self.send_status_block_wrapper()

    """
        vcom must be an int, eg. 2000 (-0.2V)
    """
    def set_vcom(self, vcom):
        byte_list = [
            254, #hdr
            *[0,0,0,0,0], #padding1
            163, #cmd
            *self.short_to_bytes(vcom, big=True),
            1, #set vcom
            0, #set power
            0, #power,
            *[0,0,0,0] #padding2
        ]
        self.write(byte_list)

    #TODO: get vcom

    def read_register(self, address, length):
        command = self.build_register_command(address, 129, length)
        return self.read_command(command, length, big=False)

    def write_register(self, address, data):
        self.write_register_generic(address, 130, data)

    """
        Note that this only works for full-width images
        It also only works if you're using firmware v.0.4
    """
    def write_register_fast(self, address, data):
        self.write_register_generic(address, 165, data)

    """
        address: byte array
        data: byte array
    """
    def write_register_generic(self, address, command, data):
        length = len(data)
        byte_list = self.build_register_command(address, command, length)
        self.write_command(byte_list, data, big=False)

    """
        address: byte array
        data: byte array
    """
    def build_register_command(self, address, command, length):
        adjusted_address = address + self.REG_ADJUST
        return [
            254, #header
            0, #padding1
            *self.int_to_bytes(adjusted_address, big=True),
            command,
            *self.short_to_bytes(length, big=True),
            *[0,0,0,0,0,0,0] #padding2
        ]

    """
    Throws an exception on failure
    """
    def send_status_block_wrapper(self):

        # a csw is 13 bytes
        length = 13

        try:
            csb_data = self.read(length)
            
            # This below line should be uncommented if we want to
            # actually do something with the status information in
            # the future.
            #return self.read_csw(csb_data, big=True)

        except Exception as e:
            raise e

    """
    command_data: list of 16 bytes
    data_transfer_length: int
    incoming: Boolean. True if reading to the usb, false if writing to it
    """
    def get_command_block_wrapper(self, command_data, data_transfer_length, incoming, big):
        flags = 128 if incoming else 0
        tag = self.get_tag()
        return self.build_cbw(
            signature = [85, 83, 66, 67],
            tag = tag,
            data_transfer_length = data_transfer_length,
            flags = flags,
            logical_unit_number = 0,
            command_length = 16,
            command_data = command_data,
            big = big
        )

    """
    Converts a list of 13 bytes into a command status wrapper.
    Return value is a list with 4 values:
    -[0] is a 4 byte array (the signature)
    -[1] is an int (the tag)
    -[2] is an int (data residue)
    -[3] is the status
    """
    def read_csw(self, bytes, big):
        signature = bytes[0:4]
        tag = self.bytes_to_int(bytes[4:8], big)
        data_residue = self.bytes_to_int(bytes[8:12], big)
        status = bytes[12]
        return [signature, tag, data_residue, status]

    """
    Builds a command block wrapper.
    Parameters are:
    signature: 4 byte array
    tag: int
    data_transfer_length: int
    flags: 1 byte
    logical_unit_number: 1 byte
    command_length: 1 byte
    command_data: 16 bytes
    """
    def build_cbw(
        self,
        signature,
        tag,
        data_transfer_length,
        flags,
        logical_unit_number,
        command_length,
        command_data,
        big
    ):
        return [
            *signature,
            *self.int_to_bytes(tag, big),
            *self.int_to_bytes(data_transfer_length, big),
            flags,
            logical_unit_number,
            command_length,
            *command_data
        ]

    def inquiry(self):

        length = 40
        bytes = self.read_command(self.INQUIRY_CMD, length, big = False)

        ignore_start = bytes[0:8]
        vendor = bytes[8:16]
        product = bytes[16:32]
        revision = bytes[32:36] #TODO read this
        ignore_end = bytes[36:40]

        return [
            vendor, #0
            product, #1
            revision #2
        ]

    # System information about epaper panel.
    def get_system_info(self):

        length = 29 * 4
        byte_list = self.read_command(self.GET_SYS_CMD, length, big = False)

        # Convert list of bytes into list of 4-byte chunks
        ints = list(self.bytes_to_ints(byte_list, big = True))

        standard_cmd_no = ints[0]
        extended_cmd_no = ints[1]
        signature = ints[2] #Always 8951 (943273265)
        version = ints[3] #Command table version
        width = ints[4] #Panel width
        height = ints[5] #Panel height
        update_buf_base = ints[6]
        image_buffer_base = ints[7] #img_addr
        temperature_no = ints[8]
        mode = ints[9] #Display mode
        frame_count = ints[10:18]
        num_img_buf = ints[18]
        reserved = ints[19:28]
        # command_table_ptr = ints[29]

        # print("Width: "+str(width))
        # print("Height: "+str(height))
        # print("Version: "+bytes(byte_list[12:16]).decode("utf-8"))
        # print("Mode: "+str(mode))

        return [
            standard_cmd_no, #0
            extended_cmd_no, #1
            signature, #2
            version, #3
            width, #4
            height, #5
            update_buf_base, #6
            image_buffer_base, #7
            temperature_no, #8
            mode, #9
            frame_count, #10
            num_img_buf #11
        ]

    def load_image_area(self, x, y, w, h, buffer):

        if len(buffer) > self.MAX_TRANSFER:
            raise ValueError(f"Buffer is too big. {len(buffer)} > {self.MAX_TRANSFER}")
        
        address = self.img_addr

        if w == self.panel_width:
            adjusted_address = address - self.REG_ADJUST + int(self.pitch * y)
            self.write_register_fast(adjusted_address, buffer)
        
        elif self.bpp == 1:
            # The LD_IMAGE_AREA_CMD command only support 8bpp images
            raise ValueError("1bpp mode only supports full-width images")

        else:
            area = self.ints_to_bytes([address, x, y, w, h], big=True)
            command = self.LD_IMAGE_AREA_CMD
            self.write_command(command, area, extra_data = buffer, big = False)

    def display_area(self, x, y, w, h, display_mode=None):

        # If not defined, use the default display mode determined by the bpp
        if display_mode is None:
            display_mode = self.display_mode
            
        wait_ready = 1
        address = self.img_addr
        display_area = self.ints_to_bytes([address, display_mode, x, y, w, h, wait_ready], big=True)
        self.write_command(self.DPY_AREA_CMD, display_area, big = False)

    def set_bpp(self, bpp):

        if bpp != 1 and bpp != 8:
            raise ValueError("The USB driver only supports 1bpp or 8bp")

        self.bpp = bpp

        # Start by calculating the panel width in double-words for 1bpp mode.
        #
        # In other words, this is the number of 4-byte aligned chunks of data
        # we would need to display a single row of pixels in 1bpp mode.
        # In 1bpp mode, a single byte represents 8 pixels.
        # So a 4-byte chunk is 32 pixels.
        #
        # To enforce 4-byte alignment, we then round the value with +31 and //32
        # because not all panels are 32px*n wide.
        # eg. an 1872 width panel would be rounded to 1888 (32px * 59).
        # That would give us panel_width_bytes_1bpp a value of 59.
        panel_width_bytes_1bpp = (self.panel_width + 31) // 32

        # Next, calculate the memory pitch.
        # The pitch is the number of bytes needed to fill 1 row of the panel.
        #
        # For 1bpp mode, this is panel_width_bytes_1bpp * 4.
        # That's because panel_width_bytes_1bpp is already the number of bytes
        # needed represented as 4-byte chunks.
        # So to get the number of bytes, we just multiple that number by 4.
        #
        # For 8bpp mode, the number of bytes needed is simply the width of the
        # panel, since in 8bpp mode 1 byte = 1 pixel.
        mem_pitch_1bpp = panel_width_bytes_1bpp * 4
        mem_pitch_8bpp = self.panel_width
        if bpp == 1:
            self.pitch = mem_pitch_1bpp
        else:
            self.pitch = mem_pitch_8bpp


        # Set the bpp and pitch registers.
        # This tells the panel whether we're in 1bpp or 8bpp mode.
        # It also puts the panel in pitch mode, if needed.
        bpp_and_pitch_mode = self.read_register(self.REG_UP1SR, 4)
        if bpp == 1:
            bpp_and_pitch_mode[2] |= 0x06
        else:
            bpp_and_pitch_mode[2] &= 0xf9
        self.write_register(self.REG_UP1SR, bpp_and_pitch_mode)

        # 1bpp color table
        # Not necessary for 8bpp, but it doesn't hurt to leave it in
        gray = 0xf0
        black = 0x00
        self.write_register(self.REG_BGVR, [gray, black])

        # Set the device width in bytes as if we were in 1bpp mode.
        # Again, probably not necessary for 8bpp mode, but it doesn't hurt.
        width_as_bytes = list(panel_width_bytes_1bpp.to_bytes(2, 'little'))
        self.write_register(self.REG_WIDTH, width_as_bytes)

        # Set the display mode to either A2 or DU
        # This should probably be set elsewhere
        if bpp == 1:
            self.display_mode = self.A2
        else:
            self.display_mode = self.DU4

        # Calculate the maximum height which a single image write could be
        # with the compressed byte data still being smaller than MAX_TRANSFER.
        #
        # For example, let's say the panel is 1872px wide.
        # In 1bpp mode this is rounded to 1888px (4-byte aligned).
        # This gives a 1bpp pitch of 236 and an 8bpp pitch of 1872.
        # MAX_TRANSFER is 61,420
        #
        # Image writes for the USB driver need to be full-width.
        # The pitch tells us the number of bytes in a full-width row.
        # So the number of bytes transferred will be a multiple of the pitch.
        #
        # In 8bpp mode:
        # 61420 / 1872 = 32.80982905982906
        # Rounded down, that's 32px.
        # So in 8bpp mode, an image chunk can be a maximum of 1872x32 pixels and
        # still be under the MAX_TRANSFER limit.
        # 1872 x 32 = 59,904px
        # 1px = 1byte in 8bpp mode, so 59,904px = 59,904 bytes.
        #
        # In 1bpp mode:
        # 61420 / 236 = 260.2542372881356
        # Rounded down, that's 260px.
        # So in 1bpp mode, an image chunk can be a maximum of 1888x260 pixels and
        # still be under the MAX_TRANSFER limit.
        # 260 * 1888 = 490,880px
        # 1px = 8 bytes in 1bpp mode, so
        # 490,880px / 8 = 61,360 bytes
        # 
        self.max_chunk_height = int(self.MAX_TRANSFER / self.pitch)
        #self.max_chunk_height = int(self.MAX_TRANSFER / (self.pitch * 8 / bpp))

        pass

    def draw(self, image, base_x, base_y, display_mode=None, refresh=True):

        h = image.height
        w = image.width

        # Iterate over the image by cutting it up into chunks.
        # Chunk sizes are determined by the max allowed chunk height.
        # This is necessary due to limitations on how much data
        # can be sent via USB commands in one go.
        for y in range(0, h, self.max_chunk_height):

            # Get a chunk of max_chunk_height in height.
            # Or, if there aren't that many pixels left, just grab
            # whatever remains of the image.
            remaining = h - y
            chunk_height = min(self.max_chunk_height, remaining)
            #print(f"Chunk height: {chunk_height}")

            # Crop the image.
            # These values are relative to the IMAGE x/y coordinates.
            x_start = 0
            y_start = y
            x_end = w
            y_end = y_start + chunk_height
            bbox = (x_start, y_start, x_end, y_end)
            cropped = image.crop(bbox)

            # Pack the bytes
            packed = self.pack_image(cropped)

            # Load the cropped image to the panel.
            # These values are relative to the PANEL x/y coordinates.
            x2_start = base_x + x_start
            y2_start = base_y + y_start
            x2_end = cropped.width
            y2_end = cropped.height
            self.load_image_area(x = x2_start, y = y2_start, w = x2_end, h = y2_end, buffer = packed)

        if refresh:
            # Finally, after all the image chunks have been loaded, refresh the panel
            self.display_area(x = base_x, y = base_y, w = w, h = h, display_mode = display_mode)


    def pack_image(self, image):

        #print(f"bpp: {self.bpp}")

        # If we're in 8bpp mode then we don't need to do anything fancy.
        # Just grab the raw bytes from a grayscale image.
        if self.bpp == 8:
            return bytearray(image.convert('L').tobytes('raw'))


        # If we're in 1bpp mode, however, then we can save a lot of data by
        # packing the image.
        # This involves taking pixels from the source image and repacking them
        # in a smaller number of bytes.
        #
        # A byte has 8 bits in it.
        # 1bpp = 1 bit per pixel.
        # 8bpp = 8 bits per pixel.
        # So 1 byte (8 bits) can hold 1 pixel in 8bpp mode, or 8 pixels in 1bpp mode.
        #
        # The source image is represented by a single byte per pixel.
        # In other words, the source is 8bpp.
        # So if we repack it as 1bpp, it will be 1/8th of the original size.
        # Because where 8 pixels used to require 8 bytes to display them, they
        # would only require 1 byte to display that much data after repacking.

        # Start by converting the image to black and white, and grabbing the bytes
        # from the image
        frame_buffer = list(image.convert('1').getdata())

        # Next, define the step.
        # This is 8 because there are 8 pixels per byte in 1bpp mode.
        # So we want to grab 8 bytes at a time from the source (frame_buffer)
        # to insert 1 byte at a time in the destination (packed_buffer).
        step = 8

        # Check if the image is cleanly divisible by 32.
        # ie. If it adheres to 4-byte alignment.
        # If not, calculate the difference.
        rem = image.width % 32

        # Define the destination buffer.
        # Start by filling it with zeros and set the size to be the pitch
        # times the image height.
        # This should be the exact number of bytes needed to display the image
        # in 1bpp mode.
        packed_buffer = [0x00] * (self.pitch * image.height)

        # Pack the image by iterating over the x axis and moving down the y axis
        # one row at a time.
        # So we're moving from left to right, top to bottom.
        pb_index = 0
        fb_index = 0
        for y in range(0, image.height):

            # Grab 8 (step) bytes at a time, since we're repacking 8 bytes into 1.
            for x in range(0, image.width, step):
                fb = frame_buffer[fb_index:fb_index+step]
                self.pack_1bpp(packed_buffer, pb_index // step, fb)
                pb_index += step
                fb_index += step

            # If the image width doesn't divide cleanly by 32, then we need to pack
            # some extra bytes at the end of each row to enforce 4-byte alignment.
            if rem > 0:
                for x in range(0, rem, step):
                    packed_buffer[pb_index // step] = 0
                    pb_index += step

        # Finally, return the packed buffer
        return packed_buffer

    """
        Packs 8 bytes, each representing one pixel, into a single byte.

        Note that this method is different for the USB driver of the IT8951 panel
        because in SPI mode the bytes are read 2 at a time and flipped.
        But in USB mode that isn't the case.
    """
    def pack_1bpp(self, packed_buffer, i, eightBytes):
        packed_buffer[i] = \
            (1 if eightBytes[0] else 0) | \
            (2 if eightBytes[1] else 0) | \
            (4 if eightBytes[2] else 0) | \
            (8 if eightBytes[3] else 0) | \
            (16 if eightBytes[4] else 0) | \
            (32 if eightBytes[5] else 0) | \
            (64 if eightBytes[6] else 0) | \
            (128 if eightBytes[7] else 0)






    """
        Below functions aren't specific to the IT8951 panel or the USB protocol.
        They're just for convenience in data conversion.
    """

    def bytes_to_int(self, value, big):
        return int.from_bytes(bytes(value[0:4]), 'big' if big else 'little')

    def bytes_to_ints(self, bytes, big):
        int_list = []
        for i in range(0, len(bytes), 4):
            four_byte_chunk = bytes[i:i+4]
            int_list += [self.bytes_to_int(four_byte_chunk, big)]
        return int_list

    def int_to_bytes(self, value, big):
        return list(value.to_bytes(4, 'big' if big else 'little'))

    def short_to_bytes(self, value, big):
        return list(value.to_bytes(2, 'big' if big else 'little'))

    def ints_to_bytes(self, ints, big):
        byte_list = []
        for int_value in ints:
            bytes_value = self.int_to_bytes(int_value, big)
            byte_list += bytes_value
        return byte_list
