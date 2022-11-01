import socket
import threading
from time import sleep
import json
import sys

"""
    Boundaries for robot delta:
        z belongs to [-7000, -4000]
        for z == -4000 x and y belongs to [-3700, 3700]
        for z == -7000 x and y belongs to [-3000, 3000]
"""

# store predictions acquired by the vision system
global queue, speed, thread_lock, queue_update_time
queue = []
queue_lock = threading.Lock()
queue_update_time = 1

"""
(x_pos, y_pos, type_of_chocolate_bar)
type_of_chocolate_bar:
    0 -> 3-bit
    1 -> Mars
    2 -> Milkyway
    3 -> Snickers
"""


# todo parse the arguments to see if we want to use simulation or real delta set global speed for all commands

class Singleton:
    __instance = None
    def __new__(cls):
        if cls.__instance is None:
            # __new__ method static method, doesn't take self
            cls.__instance = super(Singleton, cls).__new__(cls)

        return cls.__instance


class DeltaClient(Singleton):
    sock = None
    HOST, PORT = "localhost", 2137
    home_pos = "-2000-2000-4500"
    obj_hover_height = "-4500"
    obj_pickup_height = "-7000"
    offset_threshold = 100  # how much can differ the real and set position
    sleep_time = 1  # how long in seconds will the G_P command be send while checking if achieved position

    # put down location coordinates
    put_location_1 = "+1000+1000"
    put_location_2 = "+1000-1000"
    put_location_3 = "-1000+1000"
    put_location_4 = "-1000-1000"

    def __del__(self):
        if self.sock:
            self.sock.close()

    def execute_command(self, command):
        return_value = None
        prefix = command[0:3]

        if prefix == "G_P":
            self.sock.send("G_P".encode())
            recv = self.sock.recv(26).decode()
            return recv

        elif prefix == "LIN":
            print("Performing: ", command)
            set_x, set_y, set_z = self.get_coordinates(command)
            self.sock.send(command.encode())
            sleep(self.sleep_time)
            while True:
                self.sock.recv(23)  # clear buffer
                self.sock.send("G_P".encode())
                sleep(0.3)
                recv = self.sock.recv(26).decode()
                curr_x, curr_y, curr_z = self.get_coordinates(recv)
                print("Current position: ", curr_x, " ", curr_y, " ", curr_z, " ")

                offsets = []
                offsets.append(abs(set_x - curr_x))
                offsets.append(abs(set_y - curr_y))
                offsets.append(abs(set_z - curr_z))

                if max(offsets) <= self.offset_threshold:
                    break
                else:
                    self.sock.send(command.encode())
                sleep(self.sleep_time)

        elif prefix == "TIM":
            timeout = command[3:6]
            sleep(int(timeout) // 1000)
        elif prefix == "JNT":
            pass

        elif prefix == "CIR":
            pass

        sleep(self.sleep_time)
        return

    # todo update to enable controlling the suction cup and differentiate between picking up and putting down
    def pick_up_command(self, coordinates):
        pick_up = []
        pick_up.append("LIN" + coordinates + self.obj_hover_height + "TOOL_")
        pick_up.append("LIN" + coordinates + self.obj_pickup_height + "TOOL_")
        pick_up.append("TIM" + str(2000))
        pick_up.append("LIN" + coordinates + self.obj_hover_height + "TOOL_")
        return pick_up

    def putting_down_command(self, put_location):
        put_down = []
        put_down.append("LIN" + put_location + self.obj_hover_height + "TOOL_")
        put_down.append("LIN" + put_location + self.obj_pickup_height + "TOOL_")
        put_down.append("TIM" + str(2000))
        put_down.append("LIN" + put_location + self.obj_hover_height + "TOOL_")
        return put_down

    def get_coordinates(self, s):
        x = int(s[4:8]) if s[3] == "+" else int(s[3:8])
        y = int(s[9:13]) if s[8] == "+" else int(s[8:13])
        z = int(s[14:18]) if s[13] == "+" else int(s[13:18])
        # todo convert to number normalize x, y, z
        return x, y, z

    def create_commands(self, x, y, type_of):
        commands = []

        x = "+" + str(x) if x >= 0 else str(x)
        y = "+" + str(y) if y >= 0 else str(y)

        # pick up object
        commands.extend(self.pick_up_command(x + y))

        # put down to specific location and go to default position
        match type_of:
            case 0:
                commands.extend(self.putting_down_command(self.put_location_1))
            case 1:
                commands.extend(self.putting_down_command(self.put_location_2))
            case 2:
                commands.extend(self.putting_down_command(self.put_location_3))
            case 3:
                commands.extend(self.putting_down_command(self.put_location_4))

        # return home command
        commands.append("LIN" + self.home_pos + "TOOL_")
        return commands

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.HOST, self.PORT))
        sleep(1)
        print("Connected to delta")

        # go home
        self.execute_command("LIN" + self.home_pos + "TOOL_")
        commands = []

        # create commands to move every detected object
        for element in queue:
            if abs(element[0]) > 3000 or abs(element[1] > 3000):
                continue
            commands.extend(self.create_commands(element[0], element[1], element[2]))
        print(commands)

        while commands:
            self.execute_command(commands[0])
            commands.pop(0)
# used to communicate with robot delta


# used to communicate with Vision System
class VisonSystemClient(Singleton):
    sock = None
    running = False
    HOST, PORT = "localhost", 8070

    def __init__(self):
        if self.sock is None:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.running = True

    def __del__(self):
        if self.sock:
            self.sock.close()

    def start(self):
        try:
            self.sock.connect((self.HOST, self.PORT))
            self.sock.recv(1024)  # clear buffer, skip header
            print("Connected to server")
        except Exception as e:
            print(e)

    def get_data(self):
        self.sock.recv(1024).decode()  # clear buffer
        recv_str = self.sock.recv(2048).decode()
        # print("Recived data: ", recv_str)

        # parse the input
        splited_str = recv_str.split("\n")
        for s in splited_str:
            s_replaced = s.replace("\r", "")

            # compute only first valid input
            if self.is_valid_json(s_replaced):
                self.parse_data_from_string(s_replaced)
                break

        # endl = recv_str.find("\n")
        # striped_str = recv_str[:endl - 1]
        # print(striped_str, " ", type(striped_str))
        # try:
        #     json_data = json.loads(striped_str)
        #     print("From Vision System: ",  json_data)
        #
        # except:
        #     print("___")
        #     pass
        # if isinstance(striped_str, str) and striped_str[0] == "{" and striped_str[]:
        #     json_data = json.loads(striped_str)
        #     print("From Vision System: ",  json_data)
        # else:
        #     print("Not a string: ", striped_str)
        # queue_lock.acquire()

    def is_valid_json(self, s):
        if len(s) < 65:
            return False
        if s[0] != "{" or s[-1] != "}":
            return False
        return True


    def parse_data_from_string(self, s_replaced):
        dic = json.loads(s_replaced)
        local_queue = []
        for index, (name, detections) in enumerate(dic.items()):
            if detections:
                for detection in detections:
                    x, y = detection['middle_transformed']
                    # normalize x, y
                    if 0 <= x <= 416 and 0 <= x <= 416:
                        x = x / 416
                        y = y / 416
                        local_queue.append((x, y, index))


        queue_lock.acquire()
        queue = local_queue
        print(queue)
        queue_lock.release()



d1 = VisonSystemClient()
d1.start()
while d1.running:
    d1.get_data()
    print("Current queue state: ", queue)
    sleep(queue_update_time)  # how often should the queue be updated

# d2 = DeltaClient()
# d2.start()
