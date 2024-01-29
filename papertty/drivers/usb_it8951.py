# This code has partially been adapted from https://github.com/faassen/rust-it8951
# and https://github.com/blahgeek/rabbitink

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

    # maximum transfer size is 60k bytes for IT8951 USB
    # 20 bytes are used for the headers of an area load request,
    # so subtract that from 60k to give the max data chunk size
    MAX_TRANSFER = (60 * 1024) - 20

    INQUIRY_CMD = [18, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    GET_SYS_CMD = [254, 0, 56, 57, 53, 49, 128, 0, 1, 0, 2, 0, 0, 0, 0, 0]
    LD_IMAGE_AREA_CMD = [254, 0, 0, 0, 0, 0, 162, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    DPY_AREA_CMD = [254, 0, 0, 0, 0, 0, 148, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    REG_ADJUST = 0x18000000

    def __init__(self, vid, pid):
        (self.endpoint_in, self.endpoint_out, self.device) = self.find_usb_device(vid, pid)
        self.tag_num = 0

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
        return self.endpoint_in.read(length)

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

    def read_register(self, address, length):
        command = self.build_register_command(address, 129, length)
        return self.read_command(command, length, big=False)

    def write_register(self, address, data):
        self.write_register_generic(address, 130, data)

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
        revision = bytes[32:36]
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

        self.img_addr = image_buffer_base

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
            raise ValueError("Buffer is too big")
        address = self.img_addr
        area = self.ints_to_bytes([address, x, y, w, h], big=True)
        command = self.LD_IMAGE_AREA_CMD
        self.write_command(command, area, extra_data = buffer, big = False)
        #self.write_register_fast(address-self.REG_ADJUST+1872*y, buffer)

    def display_area(self, x, y, w, h, display_mode):
        wait_ready = 1
        address = self.img_addr
        display_area = self.ints_to_bytes([address, display_mode, x, y, w, h, wait_ready], big=True)
        self.write_command(self.DPY_AREA_CMD, display_area, big = False)

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
