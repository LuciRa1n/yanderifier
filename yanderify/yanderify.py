# coding: utf-8
print("If you are reading this, I'm not dead yet.")

progress = 0
progress_max = 0
stopped = True

try:
    from steghelper import ffmpeg_flags
except ImportError:
    ffmpeg_flags = False

import matplotlib
matplotlib.use('Agg')
from tkinter import *
from tkinter import filedialog
from tkinter import scrolledtext
from tkinter.ttk import *
import os
import queue
import shlex
import shutil
import subprocess
import threading
import traceback
import imageio
import torch
from skimage import img_as_ubyte, img_as_float
import skimage.transform as transform
import cv2
import numpy as np
import webbrowser
from demo import *

# written by dunnousername#8672

print('Loading checkpoints...')

checkpoints = {
    'cpu': True
}

def reload():
    global checkpoints
    demo_g, demo_kp = load_checkpoints('../fomm/config/vox-256.yaml', 'checkpoint.tar', cpu=checkpoints['cpu'])
    checkpoints['g'] = demo_g
    checkpoints['kp'] = demo_kp

reload()

print('Initializing windows...')

root = Tk()
use_cpu = IntVar()
st = None
video_in_path = None
image_in_path = None
video_out_path = None
q = queue.Queue()

run_lock = threading.Lock()

def write_noln(text):
    st.configure(state='normal')
    st.insert(END, text)
    st.configure(state='disabled')
    st.yview(END)

def write_ln():
    write_noln('\n')

def write(text):
    write_noln(text)
    write_ln()

def video_in_cb():
    global video_in_path
    x = filedialog.askopenfilename(filetypes=(('video files', '*.mp4;*.mkv;*.mov;*.avi'),))
    if x is not None:
        if len(x) > 0:
            video_in_path = x
            write('New video input path: {}'.format(video_in_path))

def image_in_cb():
    global image_in_path
    x = filedialog.askopenfilename(filetypes=(('image files', '*.jpg;*.jpeg;*.png'),))
    if x is not None:
        if len(x) > 0:
            image_in_path = x
            write('New image input path: {}'.format(image_in_path))

def video_out_cb():
    global video_out_path
    x = filedialog.asksaveasfilename(filetypes=(('.mp4 files', '*.mp4'),))
    if x is not None:
        if len(x) > 0:
            if not x.endswith('.mp4'):
                x = x + '.mp4'
            video_out_path = x
            write('New video output path: {}'.format(video_out_path))

def trace(stage, inputs, aux=None):
    sep = '==========================='
    (type_, value, tb) = sys.exc_info()
    q.put(sep)
    q.put('This section contains the details the devs need to fix this issue.')
    q.put('If you are reporting a bug, please include this entire section.')
    q.put('If you leave out any of it, there is a good chance the devs will not be able to help.')
    q.put('Error: received a {} at stage "{}".'.format(type_.__name__, stage))
    q.put('Message: "{}"'.format(str(value)))
    q.put('Full traceback:')
    for s in traceback.format_tb(tb):
        q.put(s)
    q.put('<log>')
    q.put(aux)
    q.put('</log>')
    q.put('<inputs>')
    q.put(inputs)
    q.put('</inputs>')
    q.put('This is the last line of the crash report section.')
    q.put(sep)

def acceptable_resolution(x, y):
    modulus = 16
    if not (x % modulus == 0):
        x = modulus * (x // modulus + 1)
    if not (y % modulus == 0):
        y = modulus * (y // modulus + 1)
    return x, y

# this function is from https://github.com/AliaksandrSiarohin/first-order-model/blob/master/demo.py and is slightly modified
def make_animation_modified(source_image, driving_video, generator, kp_detector, relative=True, adapt_movement_scale=True, cpu=False):
    with torch.no_grad():
        predictions = []
        source = torch.tensor(source_image[np.newaxis].astype(np.float32)).permute(0, 3, 1, 2)
        if not cpu:
            source = source.cuda()
        driving = torch.tensor(np.array(driving_video)[np.newaxis].astype(np.float32)).permute(0, 4, 1, 2, 3)
        kp_source = kp_detector(source)
        kp_driving_initial = kp_detector(driving[:, :, 0])

        global progress_max
        progress_max = driving.shape[2]
        global progress
        progress = 0
        for frame_idx in range(driving.shape[2]):
            driving_frame = driving[:, :, frame_idx]
            if not cpu:
                driving_frame = driving_frame.cuda()
            kp_driving = kp_detector(driving_frame)
            kp_norm = normalize_kp(kp_source=kp_source, kp_driving=kp_driving,
                                   kp_driving_initial=kp_driving_initial, use_relative_movement=relative,
                                   use_relative_jacobian=relative, adapt_movement_scale=adapt_movement_scale)
            out = generator(source, kp_source=kp_source, kp_driving=kp_norm)
            predictions.append(np.transpose(out['prediction'].data.cpu().numpy(), [0, 2, 3, 1])[0])
            del driving_frame
            progress += 1
        progress = 0
    return predictions

def resize(img, shape):
    return transform.resize(img, shape, anti_aliasing=True)

def worker_thread(vid0n, img0n, vid1n, cpu):
    try:
        global progress
        global progress_max
        global stopped
        with run_lock:
            if not (cpu == checkpoints['cpu']):
                q.put('Reloading checkpoints...')
                checkpoints['cpu'] = cpu
                reload()
                q.put('Finished reloading checkpoints')
            if os.path.isfile('tmp.mp4'):
                os.remove('tmp.mp4')
            q.put('Loading sources...')
            vid0r = imageio.get_reader(vid0n)
            fps = vid0r.get_meta_data()['fps']
            vid0 = []
            while True:
                try:
                    im = vid0r.get_next_data()
                except imageio.core.CannotReadFrameError:
                    break
                else:
                    vid0.append(resize(im, (256, 256))[..., :3])
            progress = 0
            progress_max = len(vid0)
            img0 = imageio.imread(img0n)
            # TODO: v is this line really neccessary? v
            #img0 = cv2.cvtColor(img0, cv2.COLOR_BGR2RGB)
            size = img0.shape[:2][::-1]
            size = acceptable_resolution(size[0], size[1])
            img0 = resize(img0, (256, 256))[..., :3]
            vid1 = []
            q.put('Sources loaded')
            for frame in make_animation_modified(img0, vid0, checkpoints['g'], checkpoints['kp'], cpu=cpu):
                vid1.append(img_as_ubyte(resize(frame, size)))
            imageio.mimsave('tmp.mp4', vid1, fps=fps)
            q.put('Muxing audio streams into output file...')
            cmd = shlex.split('ffmpeg -y -hide_banner -loglevel warning -i tmp.mp4 -i')
            cmd.append(vid0n)
            cmd.extend(shlex.split('-map 0:v -map 1:a -movflags faststart -c:v libx264 -pix_fmt yuv420p -x264-params "nal-hrd=cbr" -b:v 1200K -minrate 1200K -maxrate 1200K -bufsize 2M'))
            if ffmpeg_flags:
                cmd.extend(shlex.split(ffmpeg_flags))
            cmd.append(vid1n)
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            q.put(output)
            #os.remove('tmp.mp4')
    except subprocess.CalledProcessError as e:
        msg = 'command "{}" returned non-zero error code {}: {}'.format(
            e.cmd,
            e.returncode,
            e.output
        )
        trace('ffmpeg', [vid0n, img0n, vid1n], aux=msg)
        q.put('ffmpeg crashed!')
        q.put('usually this means the deepfake process worked, but re-encoding failed.')
        shutil.copy('tmp.mp4', vid1n)
        q.put('you can attempt to salvage your progress by re-muxing audio streams manually.')
        q.put('this may also happen if your input video contains no audio; if this is the case, the file should be at least mostly intact.')
        raise e
    except Exception as e:
        msg = 'cpu={}'.format(cpu)
        trace('predict', [vid0n, img0n, vid1n], aux=msg)
        q.put('yanderify crashed!')
        q.put('some common problems:')
        q.put('- you have an AMD card. AMD cards are not supported in GPU mode for technical reasons. However, you can run in CPU mode, albeit much lower. Please read the disclaimer at the top about CPUs!')
        q.put('- you have an NVIDIA card, but there is either not enough VRAM or the card is too old. >=700-series cards with >=2GB dedicated VRAM should work fine')
        q.put('- you have a working card, but there is not enough available VRAM to run the deepfake process. Browsers commonly cause VRAM issues. If you have any games open, try closing them.')
        q.put('- one of the devs fucked up somewhere. if that is the case, make sure to submit the full crash report (you might have to scroll up!), otherwise we cannot help you!')
        raise e
    except KeyboardInterrupt as e:
        q.put('Stopping...')
    else:
        q.put('success!')
    finally:
        stopped = True

def start():
    global stopped
    if not stopped:
        stopped = True
        return
    write('starting...')
    if (video_in_path is None) or (image_in_path is None) or (video_out_path is None):
        write('error: files must be selected')
        return
    if run_lock.locked():
        write('error: already started!')
        return
    stopped = False
    threading.Thread(target=worker_thread, args=(video_in_path, image_in_path, video_out_path, use_cpu.get())).start()

def show_kitty():
    webbrowser.open('https://thiscatdoesnotexist.com/')

class Yanderify(Frame):
    def __init__(self, master=None):
        super().__init__(master)
        self.master = master
        self.grid()
        self.create_widgets()
        self.after(50, self.process_queue)

    def create_widgets(self):
        global st
        master = self.master
        c = Checkbutton(master, text='I don\'t have NVIDIA >=GTX950', variable=use_cpu)
        c.grid(row=0, column=0)
        video_in = Button(master, text='Select Video', command=video_in_cb)
        video_in.grid(row=0, column=1)
        image_in = Button(master, text='Select Image', command=image_in_cb)
        image_in.grid(row=0, column=2)
        video_out = Button(master, text='Select Output', command=video_out_cb)
        video_out.grid(row=0, column=3)
        kitty_button = Button(master, text='∞ kitties', command=show_kitty)
        kitty_button.grid(row=0, column=4)
        self.go = Button(master, text='Go', command=start)
        self.go.grid(row=1, column=4)
        self.progress_bar = Progressbar(master, orient=HORIZONTAL, mode='determinate', length=500)
        self.progress_bar.grid(row=1, column=0, columnspan=4)
        st = scrolledtext.ScrolledText(master, state=DISABLED)
        st.grid(row=2, column=0, columnspan=5, rowspan=7)
        write('Started Yanderify 3.0.0-alpha-0')
        write('Warning: This is not a stable release and should not be treated as such.')
        write('Disclaimer: CPU mode on low-end computers or most laptops generally will cause the system to lock-up.')
        write('We are not liable if you freeze your PC by refusing to listen to this advice.')
        write('Written by dunnousername#86??__Æ¶∬∬rundll32∟err⁉ro▚▒◑◑➽unexpe')
        write('heavily inspired by windy\'s efforts')

    def process_queue(self):
        self.progress_bar['value'] = 100 * min(1.0, progress / max(progress_max, 1.0))
        self.go['text'] = 'Go' if stopped else 'Stop'
        try:
            while True:
                msg = q.get(block=False)
                write(msg)
        except queue.Empty:
            self.after(50, self.process_queue)

app = Yanderify(master=root)
app.mainloop()