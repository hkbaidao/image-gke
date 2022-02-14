# image-gke
1 在gcs创建一个bucket用来存放图片           
2 将文件打包成镜像文件：               
（1）	Cd /home/image-gke/docker_                      
（2）	Docker build –t gcr.io/项目名/包名 .                   
（3）	Gcloud docker  -- push gcr.io/项目名/包名                     
（4）	Sudo vim/home/image-gke/gke/deployment.yaml              
      将images 修改成上边生成的镜像名称，将env内容中的bucket_name的value修改成上边创建的bucket的名字
（5） kubectl apply  -f /image-gke/gke/*                       
3 打开由程序创建的glb，添加外部访问的ip地址
