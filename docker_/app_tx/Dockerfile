FROM python:3.7

RUN apt-get update && apt-get install -y python3 python3-pip git autotools-dev automake pkg-config libtool g++ make libde265-dev libx265-dev wget

# Copy local code to the container image.
ENV APP_HOME /app
WORKDIR $APP_HOME
COPY . ./

RUN pip install Flask gunicorn

RUN git clone https://github.com/strukturag/libheif && \
    cd libheif/ && \
    ./autogen.sh && \
    ./configure && \
    make && \
    make install
RUN apt-get install -y curl libglib2.0-dev libexpat1-dev libjpeg-dev
RUN wget https://github.com/libvips/libvips/releases/download/v8.9.0/vips-8.9.0.tar.gz && \
    tar -xf vips-8.9.0.tar.gz && \
    cd vips-8.9.0 && \
    ./configure && \
    make && \
    make install && \
    ldconfig
RUN curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py && \
    python3 get-pip.py
RUN apt-get install -y python3 python-dev python3-dev \
               build-essential libssl-dev libffi-dev \
               libxml2-dev libxslt1-dev zlib1g-dev 
RUN curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py &&  python get-pip.py
RUN pip install Werkzeug cloudstorage google-cloud-datastore pyvips Pillow google-cloud-storage google-cloud-pubsub

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 app:app
