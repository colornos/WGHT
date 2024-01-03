#!/usr/bin/python3

import sys
import pygatt.backends
import logging
from logging.handlers import RotatingFileHandler
from configparser import ConfigParser
import time
import subprocess
from struct import *
import os
import threading
import urllib3
import urllib.parse

# Set up log rotation
log_file = '/home/pi/Start/sensor_manager.log'
max_log_size = 10 * 1024 * 1024  # 10 MB
backup_count = 3  # Keep three backup files

# Configure logging with rotation
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(funcName)s %(message)s',
    datefmt='%a, %d %b %Y %H:%M:%S',
    handlers=[RotatingFileHandler(log_file, maxBytes=max_log_size, backupCount=backup_count)]
)
log = logging.getLogger(__name__)

# Global variables
ERROR_RECOVERY_DELAY = 10  # seconds

# Plugin Code
class Plugin:
    def __init__(self):
        self.http = urllib3.PoolManager()

    def get_pi_info(self):
        # Function to extract specific Raspberry Pi info
        pi_info = {'hardware': '', 'revision': '', 'serial': '', 'model': ''}
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Hardware'):
                        pi_info['hardware'] = line.strip().split(': ')[1].strip()
                    elif line.startswith('Revision'):
                        pi_info['revision'] = line.strip().split(': ')[1].strip()
                    elif line.startswith('Serial'):
                        pi_info['serial'] = line.strip().split(': ')[1].strip()
                    elif line.startswith('Model'):
                        pi_info['model'] = line.strip().split(': ')[1].strip()
        except Exception as e:
            logging.getLogger(__name__).error("Error reading Raspberry Pi info: " + str(e))
        return pi_info

    def execute(self, config, persondata, weightdata, bodydata):
        log = logging.getLogger(__name__)
        log.info('Starting plugin: ' + __name__)

        pi_info = self.get_pi_info()  # Get Raspberry Pi info

        with open("/home/pi/Start/rfid.txt", "r") as f1:
            rfid = f1.read().strip()

        with open("/home/pi/Start/pin.txt", "r") as f3:
            pin = f3.read().strip()

        if not rfid:
            print("No card")
            with open("/home/pi/Start/plugin_response.txt", "w") as f2:
                f2.write("No card")
        else:
            weight = weightdata[0]['weight']  # Retrieve the latest weight value
            headers = {
                'User-Agent': 'RaspberryPi/WGHT.py',
                'Content-Type': 'application/x-www-form-urlencoded'
            }

            # Prepare form data with weight data and specific Raspberry Pi info
            form_data = {
                'rfid': rfid,
                'one': weight,
                'pin': pin,
                'hardware': pi_info['hardware'],
                'revision': pi_info['revision'],
                'serial': pi_info['serial'],
                'model': pi_info['model']
            }

            encoded_data = urllib.parse.urlencode(form_data)
            r = self.http.request('POST', 'https://colornos.com/sensors/weight.php', body=encoded_data, headers=headers)
            response = r.data.decode('utf-8')
            with open("/home/pi/Start/plugin_response.txt", "w") as f2:
                f2.write(response)
            log.info('Finished plugin: ' + __name__)
            return response

# Main Script Code
Char_person = '00008a82-0000-1000-8000-00805f9b34fb'  # person data
Char_weight = '00008a21-0000-1000-8000-00805f9b34fb'  # weight data
Char_body = '00008a22-0000-1000-8000-00805f9b34fb'  # body data
Char_command = '00008a81-0000-1000-8000-00805f9b34fb'  # command register

def decodePerson(handle, values):
    data = unpack('BxBxBBBxB', bytes(values[0:9]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x84)
    retDict["person"] = data[1]
    if data[2] == 1:
        retDict["gender"] = "male"
    else:
        retDict["gender"] = "female"
    retDict["age"] = data[3]
    retDict["size"] = data[4]
    if data[5] == 3:
        retDict["activity"] = "high"
    else:
        retDict["activity"] = "normal"
    return retDict

def decodeWeight(handle, values):
    data = unpack('<BHxxIxxxxB', bytes(values[0:14]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x1d)
    retDict["weight"] = data[1]/100.0
    retDict["timestamp"] = sanitize_timestamp(data[2])
    retDict["person"] = data[3]
    return retDict

def decodeBody(handle, values):
    data = unpack('<BIBHHHHH', bytes(values[0:16]))
    retDict = {}
    retDict["valid"] = (data[0] == 0x6f)
    retDict["timestamp"] = sanitize_timestamp(data[1])
    retDict["person"] = data[2]
    retDict["kcal"] = data[3]
    retDict["fat"] = (0x0fff & data[4])/10.0
    retDict["tbw"] = (0x0fff & data[5])/10.0
    retDict["muscle"] = (0x0fff & data[6])/10.0
    retDict["bone"] = (0x0fff & data[7])/10.0
    return retDict

def sanitize_timestamp(timestamp):
    retTS = 0
    if timestamp + time_offset < sys.maxsize:
        retTS = timestamp + time_offset
    else:
        retTS = timestamp
    if timestamp >= sys.maxsize:
        retTS = 0
    return retTS

def appendBmi(size, weightdata):
    size = size / 100.00
    for element in weightdata:
        if size == 0:
            element['bmi'] = 0
        else:
            element['bmi'] = round(element['weight'] / (size * size), 1)

def processIndication(handle, values):
    if handle == handle_person:
        result = decodePerson(handle, values)
        if result not in persondata:
            log.info(str(result))
            persondata.append(result)
        else:
            log.info('Duplicate persondata record')
    elif handle == handle_weight:
        result = decodeWeight(handle, values)
        if result not in weightdata:
            log.info(str(result))
            weightdata.append(result)
        else:
            log.info('Duplicate weightdata record')
    elif handle == handle_body:
        result = decodeBody(handle, values)
        if result not in bodydata:
            log.info(str(result))
            bodydata.append(result)
        else:
            log.info('Duplicate bodydata record')
    else:
        log.debug('Unhandled Indication encountered')

def wait_for_device(devname, timeout=30):
    found = False
    start_time = time.time()
    while not found and (time.time() - start_time) < timeout:
        try:
            found = adapter.filtered_scan(devname)
            if found:
                log.info(f"Device {devname} found.")
            else:
                log.debug(f"Device {devname} not found. Retrying...")
            time.sleep(1)  # Sleep for 1 second before retrying
        except pygatt.exceptions.BLEError as e:
            log.error(f"BLE error encountered: {e}. Resetting adapter.")
            adapter.reset()
            time.sleep(5)  # Sleep for a brief period after resetting
    return found

def connect_device(address):
    device_connected = False
    tries = 3
    device = None
    while not device_connected and tries > 0:
        try:
            device = adapter.connect(address, 8, addresstype)
            device_connected = True
        except pygatt.exceptions.NotConnectedError:
            tries -= 1
    return device

def init_ble_mode():
    p = subprocess.Popen("sudo btmgmt le on", stdout=subprocess.PIPE, shell=True)
    (output, err) = p.communicate()
    if not err:
        log.info(output)
        return True
    else:
        log.info(err)
        return False

config = ConfigParser()
config.read('/home/pi/Start/WGHT/WGHT.ini')

# Logging setup
numeric_level = getattr(logging, config.get('Program', 'loglevel').upper(), None)
if not isinstance(numeric_level, int):
    raise ValueError('Invalid log level: %s' % loglevel)
logging.basicConfig(level=numeric_level, format='%(asctime)s %(levelname)-8s %(funcName)s %(message)s', datefmt='%a, %d %b %Y %H:%M:%S', filename=config.get('Program', 'logfile'), filemode='w')
log = logging.getLogger(__name__)
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(numeric_level)
formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(funcName)s %(message)s')
ch.setFormatter(formatter)
log.addHandler(ch)

# BLE device configuration
ble_address = config.get('WGHT', 'ble_address')
device_name = config.get('WGHT', 'device_name')
device_model = config.get('WGHT', 'device_model')

# Set BLE address type and time offset based on the device model
if device_model == 'BS410':
    addresstype = pygatt.BLEAddressType.public
    time_offset = 1262304000
elif device_model == 'BS444':
    addresstype = pygatt.BLEAddressType.public
    time_offset = 1262304000
else:
    addresstype = pygatt.BLEAddressType.random
    time_offset = 0

# Start script and initialize BLE mode
log.info('WGHT Started')
if not init_ble_mode():
    sys.exit()

# Initialize BLE adapter
adapter = pygatt.backends.GATTToolBackend()
adapter.start()

plugin = Plugin()
CHECK_INTERVAL = 45  # seconds
MAX_RETRY_COUNT = 10  # Maximum number of retries for connecting to the sensor
RETRY_DELAY = 20  # Delay in seconds between retries
ADAPTER_RESET_DELAY = 25  # Delay after resetting the BLE adapter

# Main loop
while True:
    try:
        if wait_for_device(device_name, timeout=30):
            retry_count = 0
            while retry_count < MAX_RETRY_COUNT:
                device = connect_device(ble_address)
                if device:
                    persondata = []
                    weightdata = []
                    bodydata = []
                    try:
                        handle_person = device.get_handle(Char_person)
                        handle_weight = device.get_handle(Char_weight)
                        handle_body = device.get_handle(Char_body)
                        handle_command = device.get_handle(Char_command)
                        continue_comms = True
                    except pygatt.exceptions.NotConnectedError:
                        log.warning('Error getting handles')
                        continue_comms = False

                    if not continue_comms:
                        break

                    try:
                        device.subscribe(Char_weight, callback=processIndication, indication=True)
                        device.subscribe(Char_body, callback=processIndication, indication=True)
                        device.subscribe(Char_person, callback=processIndication, indication=True)
                    except pygatt.exceptions.NotConnectedError:
                        continue_comms = False

                    if continue_comms:
                        timestamp = bytearray(pack('<I', int(time.time() - time_offset)))
                        timestamp.insert(0, 2)
                        try:
                            device.char_write_handle(handle_command, timestamp, wait_for_response=True)
                        except pygatt.exceptions.NotificationTimeout:
                            pass
                        except pygatt.exceptions.NotConnectedError:
                            continue_comms = False

                        if continue_comms:
                            log.info('Waiting for notifications for another 30 seconds')
                            time.sleep(30)
                            # Insert additional code for handling the scale data here if necessary

                            try:
                                device.disconnect()
                            except pygatt.exceptions.NotConnectedError:
                                log.info('Sensor disconnected or not reachable.')

                            log.info('Done receiving data from scale')
                            if persondata and weightdata and bodydata:
                                weightdatasorted = sorted(weightdata, key=lambda k: k['timestamp'], reverse=True)
                                appendBmi(persondata[0]['size'], weightdata)
                                bodydatasorted = sorted(bodydata, key=lambda k: k['timestamp'], reverse=True)

                                plugin.execute(config, persondata, weightdatasorted, bodydatasorted)

                        break  # Successful operation, exit retry loop
                    else:
                        retry_count += 1
                        log.warning(f"Retry {retry_count} for connecting to the sensor.")
                        time.sleep(RETRY_DELAY)

                if retry_count >= MAX_RETRY_COUNT:
                    log.error("Max retries reached. Unable to connect to the sensor.")
                    break

        else:
            log.debug("Sensor not active. Will check again later.")

        time.sleep(CHECK_INTERVAL)

    except pygatt.exceptions.BLEError as ble_error:
        log.error(f"BLE Error encountered: {ble_error}. Resetting adapter.")
        adapter.reset()
        time.sleep(ADAPTER_RESET_DELAY)
    except Exception as e:
        log.error(f"An error occurred: {e}")
        time.sleep(ERROR_RECOVERY_DELAY)
