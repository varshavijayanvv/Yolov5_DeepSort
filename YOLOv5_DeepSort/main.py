from yolov5.utils.general import (
    check_img_size, non_max_suppression, scale_coords, xyxy2xywh)
from yolov5.utils.torch_utils import select_device, time_synchronized
from yolov5.utils.datasets import letterbox

from utils_ds.parser import get_config
from utils_ds.draw import draw_boxes
from deep_sort import build_tracker

import argparse
import os
import time
import numpy as np
import warnings
import cv2
import torch
import torch.backends.cudnn as cudnn

import sys

currentUrl = os.path.dirname(__file__)
sys.path.append(os.path.abspath(os.path.join(currentUrl, 'yolov5')))


cudnn.benchmark = True


class VideoTracker(object):
    def __init__(self, args):
        print('Initialize DeepSORT & YOLO-V5')
        # ***************** Initialize ******************************************************
        self.args = args

        self.img_size = args.img_size                   # image size in detector, default is 640
        self.frame_interval = args.frame_interval       # frequency

        self.device = select_device(args.device)
        self.half = self.device.type != 'cpu'  # half precision only supported on CUDA

        # create video capture ****************
        if args.display:
            cv2.namedWindow("test", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("test", args.display_width, args.display_height)

        if args.cam != -1:
            print("Using webcam " + str(args.cam))
            self.vdo = cv2.VideoCapture(args.cam)
        else:
            self.vdo = cv2.VideoCapture()

        # ***************************** initialize DeepSORT **********************************
        cfg = get_config()
        cfg.merge_from_file(args.config_deepsort)

        use_cuda = self.device.type != 'cpu' and torch.cuda.is_available()
        self.deepsort = build_tracker(cfg, use_cuda=use_cuda)

        # ***************************** initialize YOLO-V5 **********************************
        self.detector = torch.load(args.weights, map_location=self.device)['model'].float()  # load to FP32
        self.detector.to(self.device).eval()
        if self.half:
            self.detector.half()  # to FP16

        self.names = self.detector.module.names if hasattr(self.detector, 'module') else self.detector.names

        print('Done..')
        if self.device == 'cpu':
            warnings.warn("Running in cpu mode which maybe very slow!", UserWarning)

    def __enter__(self):
        # ************************* Load video from camera *************************
        if -1 != -1:
            print('Camera ...')
            ret, frame = self.vdo.read()
            assert ret, "Error: Camera error"
            self.im_width = int(self.vdo.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.im_height = int(self.vdo.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # ************************* Load video from file *************************
        else:
            assert os.path.isfile(self.args.input_path), "Path error"
            self.vdo.open(self.args.input_path)
            self.im_width = int(self.vdo.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.im_height = int(self.vdo.get(cv2.CAP_PROP_FRAME_HEIGHT))
            assert self.vdo.isOpened()
            print('Done. Load video file ', self.args.input_path)

        # ************************* create output *************************
        if self.args.save_path:
            os.makedirs(self.args.save_path, exist_ok=True)
            # path of saved video and results
            self.save_video_path = os.path.join(self.args.save_path, "results.mp4")

            # create video writer
            fourcc = cv2.VideoWriter_fourcc(*self.args.fourcc)
            self.writer = cv2.VideoWriter(self.save_video_path, fourcc,
                                          self.vdo.get(cv2.CAP_PROP_FPS), (self.im_width, self.im_height))
            print('Done. Create output file ', self.save_video_path)

        if self.args.save_txt:
            os.makedirs(self.args.save_txt, exist_ok=True)

        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.vdo.release()
        self.writer.release()
        if exc_type:
            print(exc_type, exc_value, exc_traceback)

    def run(self):
        yolo_time, sort_time, avg_fps = [], [], []
        t_start = time.time()

        idx_frame = 0
        last_out = None
        while self.vdo.grab():
            # Inference *********************************************************************
            t0 = time.time()
            _, img0 = self.vdo.retrieve()

            if idx_frame % self.args.frame_interval == 0:
                outputs, yt, st = self.image_track(img0)        # (#ID, 5) x1,y1,x2,y2,id
                last_out = outputs
                yolo_time.append(yt)
                sort_time.append(st)
                print('Frame %d Done. YOLO-time:(%.3fs) SORT-time:(%.3fs)' % (idx_frame, yt, st))
            else:
                outputs = last_out  # directly use prediction in last frames
            t1 = time.time()
            avg_fps.append(t1 - t0)

            # post-processing ***************************************************************
            # visualize bbox  ********************************
            if len(outputs) > 0:
                bbox_xyxy = outputs[:, :4]
                identities = outputs[:, -1]
                img0 = draw_boxes(img0, bbox_xyxy, identities)  # BGR

                # add FPS information on output video
                text_scale = max(1, img0.shape[1] // 1600)
                cv2.putText(img0, 'frame: %d fps: %.2f ' % (idx_frame, len(avg_fps) / sum(avg_fps)),
                        (20, 20 + text_scale), cv2.FONT_HERSHEY_PLAIN, text_scale, (0, 0, 255), thickness=2)

            # display on window ******************************
            if self.args.display:
                cv2.imshow("test", img0)
                if cv2.waitKey(1) == ord('q'):  # q to quit
                    cv2.destroyAllWindows()
                    break

            # save to video file *****************************
            if self.args.save_path:
                self.writer.write(img0)

            if self.args.save_txt:
                with open(self.args.save_txt + str(idx_frame).zfill(4) + '.txt', 'a') as f:
                    for i in range(len(outputs)):
                        x1, y1, x2, y2, idx = outputs[i]
                        f.write('{}\t{}\t{}\t{}\t{}\n'.format(x1, y1, x2, y2, idx))



            idx_frame += 1

        print('Avg YOLO time (%.3fs), Sort time (%.3fs) per frame' % (sum(yolo_time) / len(yolo_time),
                                                            sum(sort_time)/len(sort_time)))
        t_end = time.time()
        print('Total time (%.3fs), Total Frame: %d' % (t_end - t_start, idx_frame))
        return "Sucess!........"

    def image_track(self, im0):
        """
        :param im0: original image, BGR format
        :return:
        """
        # preprocess ************************************************************
        # Padded resize
        img = letterbox(im0, new_shape=self.img_size)[0]
        # Convert
        img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, to 3x416x416
        img = np.ascontiguousarray(img)

        # numpy to tensor
        img = torch.from_numpy(img).to(self.device)
        img = img.half() if self.half else img.float()  # uint8 to fp16/32
        img /= 255.0  # 0 - 255 to 0.0 - 1.0
        if img.ndimension() == 3:
            img = img.unsqueeze(0)
        s = '%gx%g ' % img.shape[2:]    # print string

        # Detection time *********************************************************
        # Inference
        t1 = time_synchronized()
        with torch.no_grad():
            pred = self.detector(img, augment=self.args.augment)[0]  # list: bz * [ (#obj, 6)]

        # Apply NMS and filter object other than person (cls:0)
        pred = non_max_suppression(pred, 0.5, 0.5,
                                   classes=self.args.classes, agnostic=self.args.agnostic_nms)
        t2 = time_synchronized()

        # get all obj ************************************************************
        det = pred[0]  # for video, bz is 1
        if det is not None and len(det):  # det: (#obj, 6)  x1 y1 x2 y2 conf cls

            # Rescale boxes from img_size to original im0 size
            det[:, :4] = scale_coords(img.shape[2:], det[:, :4], im0.shape).round()

            # Print results. statistics of number of each obj
            for c in det[:, -1].unique():
                n = (det[:, -1] == c).sum()  # detections per class
                s += '%g %ss, ' % (n, self.names[int(c)])  # add to string
            print('Frame %ss Done.' % (s))
            bbox_xywh = xyxy2xywh(det[:, :4]).cpu()
            # print('Frame %ss Done.' % (bbox_xywh))
	        #print('Frame %s Done and wd: %ss ' % (s,xyxy2xywh(det[:, :4])))
            confs = det[:, 4:5].cpu()

            # ****************************** deepsort ****************************
            outputs = self.deepsort.update(bbox_xywh, confs, im0)
            #print('dddddd %ss' %(outputs[0]))
	      # print(outputs[0])

        else:
            outputs = torch.zeros((0, 5))

        t3 = time.time()
        return outputs, t2-t1, t3-t2


def predictYolo(parser):

        # agnostic_nms= False

        args = parser.parse_args()
        args.agnostic_nms = False

        # Input Test video file path
        # args.weights = args.weights
        # print("Test video file path ", args.input_path)
        # # Input Test video file path
        # args.input_path = args.input_path
        # print("Test video file path ",args.input_path)
        # args.input_path = args.input_path
        args.img_size = check_img_size(args.img_size)

        print(args)

        with VideoTracker(args) as vdo_trk:
            vdo_trk.run()
        return "Success"


