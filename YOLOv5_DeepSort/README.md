# YOLOv5_DeepSort

## Prepare 
1 Create a virtual environment with Python >=3.8  
------------
conda create -n py38 python=3.8    
conda activate py38   
------------

2 Install pytorch >= 1.6.0, torchvision >= 0.7.0.
------------
conda install pytorch torchvision cudatoolkit=10.1 -c pytorch
------------


3 Install all dependencies
------------
pip install -r requirements.txt
------------

4 Download the yolov5 weight. 
I already put the 'yolov5s.pt' inside YOLOv5_DeepSort/yolov5/weights . 


## Run

python app.py 

Output Video files : YOLOv5_DeepSort/output
Output txt files   : YOLOv5_DeepSort/output/predict



## Reference
1) https://github.com/HowieMa/DeepSORT_YOLOv5_Pytorch
  
2) https://github.com/mikel-brostrom/Yolov5_DeepSort_Pytorch
 
