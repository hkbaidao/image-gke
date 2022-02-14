import os
# import pyvips
import re
import mimetypes

from flask import Flask, request, make_response, send_file, Response
from PIL import Image, ImageDraw, ImageSequence
from werkzeug.routing import BaseConverter

from google.cloud import storage

from PIL import ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

app = Flask(__name__)

IMAGE_INFO = "imageInfo"
IMAGE_VIEW = "imageView2"
EXIF = "exif"
IMAGE_MOGR = "imageMogr2"
WATER_MARK = "watermark"
IMAGE_AVE = "imageAve"


def item_index(arr, item):
    """
    获取元素在列表中的索引
    :param arr:
    :param item:
    :return:
    """
    for i, value in enumerate(arr):
        if value == item:
            return i
    return


def merge_dict(source, target):
    """
    合并两个字典。合并后的字典的value是一个列表。
    eg:
    doog_1 = {name: 'wangwang', age: 10}
    doog_2 = {name: 'wang~', gender: '♂'}
    合并以后
    doog = {name: ['wangwang', 'wang~'], age: 10, gender: '♂'}
    :param source:
    :param target:
    :return:
    """
    # keys = [key for key in source]
    keys = list(source)[:]
    keys += [key for key in target if not key in keys]
    for key in keys:
        v1 = source.get(key, [])
        v2 = target.get(key, [])

        if not isinstance(v1, list):
            v1 = [v1]

        if isinstance(v2, list):
            v1 += v2
        else:
            v1.append(v2)

        source[key] = v1

    return source


def parse_qs(query):
    if not query:
        return

    encoded = {}
    args = query.split("/")

    interface = args[0]
    if IMAGE_INFO == interface:
        encoded["interface"] = IMAGE_INFO

    elif IMAGE_VIEW == interface:
        if len(args) <= 2:
            return
        encoded["interface"] = IMAGE_VIEW
        encoded["mode"] = args[1]
        # ["w", 2, "h", 2] ==> {"w": 2, "h": 2}
        params = dict(zip(*2 * (iter(args[2:]),)))
        merge_dict(encoded, params)

    elif EXIF == interface:
        encoded["interface"] = EXIF

    elif IMAGE_MOGR == interface:
        encoded["interface"] = IMAGE_MOGR
        encoded["auto-orient"] = str("auto-orient" in args)
        encoded["strip"] = str("strip" in args)
        encoded["blur"] = str("blur" in args)

        args_name = ["thumbnail", "gravity", "crop", "rotate", "format", "interlace"]
        for arg_name in args_name:
            if arg_name in args:
                try:
                    encoded[arg_name] = args[item_index(args, arg_name) + 1]
                except IndexError:
                    pass
                except TypeError:
                    pass  # NoneType

    elif WATER_MARK == interface:
        if len(args) <= 2:
            return
        encoded["interface"] = WATER_MARK
        encoded["mode"] = args[1]
        params = dict(zip(*2 * (iter(args[2:]),)))
        merge_dict(encoded, params)
    elif IMAGE_AVE == interface:
        encoded["interface"] = IMAGE_AVE

    else:
        return
    return encoded


def image_view_mode_1(im, w, h):
    """
    限定缩略图的宽最少为<Width>，高最少为<Height>，进行等比缩放，居中裁剪。
    转后的缩略图通常恰好是 <Width>x<Height> 的大小（有一个边缩放的时候会因为超出矩形框而被裁剪掉多余部分）。
    如果只指定 w 参数或只指定 h 参数，代表限定为长宽相等的正方图。
    """
    if not w and not h:
        return

    size = im.size
    if not w:
        h = int(h)
        w = min(h, size[0])
    if not h:
        w = int(w)
        h = min(w, size[1])

    w = int(w)
    h = int(h)

    ratio_w = w / size[0]
    ratio_h = h / size[1]
    max_ratio = max(ratio_w, ratio_h)
    min_ratio = min(ratio_w, ratio_h)

    if min_ratio >= 1:  # 两边都大
        return im

    if max_ratio < 1:  # 两边均小于原来
        # 新规格
        size = resize = tuple(int(x * max_ratio) for x in size)
        im = im.resize(resize)
    box = []
    box.append(int((size[0] - w) / 2))
    box.append(int((size[1] - h) / 2))
    box.append(w + box[0])
    box.append(h + box[1])

    im = im.crop(tuple(box))
    return im


def image_view_mode_2(im, w, h):
    """
    限定缩略图的宽最多为<Width>，高最多为<Height>，进行等比缩放，不裁剪。
    如果只指定 w 参数则表示限定宽度（高度自适应），只指定 h 参数则表示限定高度（宽度自适应）。
    它和模式0类似，区别只是限定宽和高，不是限定长边和短边。
    从应用场景来说，模式0适合移动设备上做缩略图，模式2适合PC上做缩略图。
    eg:
    """
    if not w and not h:
        return

    size = im.size
    ratio_w = ratio_h = 1
    if w:
        w = int(w)
        ratio_w = w / size[0]
    if h:
        h = int(h)
        ratio_h = h / size[1]

    min_ratio = min(ratio_w, ratio_h)
    if min_ratio >= 1:
        return im

    resize = tuple(int(x * min_ratio) for x in size)
    im = im.resize(resize)
    return im


def image_view_mode_3(im, w, h):
    """
    限定缩略图的宽最少为<Width>，高最少为<Height>，进行等比缩放，不裁剪。
    """
    if not w and not h:
        return

    size = im.size
    if not w:
        w = h
    if not h:
        h = w

    w = int(w)
    h = int(h)

    ratio_w = w / size[0]
    ratio_h = h / size[1]
    max_ratio = max(ratio_w, ratio_h)
    if max_ratio >= 1:
        return im

    resize = tuple(int(x * max_ratio) for x in size)
    im = im.resize(resize)
    return im


def image_view_mode_4(im, long_edge, short_edge):
    """
    限定缩略图的长边最少为<LongEdge>，短边最少为<ShortEdge>，进行等比缩放，不裁剪。
    这个模式很适合在手持设备做图片的全屏查看（把这里的长边短边分别设为手机屏幕的分辨率即可），
    生成的图片尺寸刚好充满整个屏幕（某一个边可能会超出屏幕）。
    """
    if not long_edge and not short_edge:
        return
    size = im.size
    origin_long_edge = max(size)
    origin_short_edge = min(size)

    if not long_edge:
        long_edge = short_edge
    if not short_edge:
        short_edge = long_edge

    long_edge = int(long_edge)
    short_edge = int(short_edge)

    ratio_long = long_edge / origin_long_edge
    ratio_short = short_edge / origin_short_edge

    max_ratio = max(ratio_long, ratio_short)
    if max_ratio >= 1:
        return im

    resize = tuple(int(x * max_ratio) for x in size)
    im = im.resize(resize)
    return im


def image_view_mode_5(im, long_edge, short_edge):
    """
    限定缩略图的长边最少为<LongEdge>，短边最少为<ShortEdge>，进行等比缩放，居中裁剪。
    同上模式4，但超出限定的矩形部分会被裁剪。
    """
    if not long_edge and not short_edge:
        return

    size = im.size
    origin_long_edge = max(size)
    origin_short_edge = min(size)

    if not long_edge:
        short_edge = int(short_edge)
        long_edge = short_edge
    if not short_edge:
        long_edge = int(long_edge)
        short_edge = long_edge

    long_edge = min(int(long_edge), origin_long_edge)
    short_edge = min(int(short_edge), origin_short_edge)

    ratio_long = long_edge / origin_long_edge
    ratio_short = short_edge / origin_short_edge
    min_ratio = min(ratio_long, ratio_short)
    max_ratio = max(ratio_long, ratio_short)

    if min_ratio >= 1:
        return im

    box = []
    if max_ratio < 1:
        size = resize = tuple(int(x * max_ratio) for x in size)
        im = im.resize(resize)

    if size[0] >= size[1]:  # 横向
        box.append(int((size[0] - long_edge) / 2))
        box.append(int((size[1] - short_edge) / 2))
        box.append(box[0] + long_edge)
        box.append(box[1] + short_edge)
    else:  # 竖向
        box.append(int((size[0] - short_edge) / 2))
        box.append(int((size[1] - long_edge) / 2))
        box.append(box[0] + short_edge)
        box.append(box[1] + long_edge)

    im = im.crop(tuple(box))
    return im


def image_view_mode_6(ima, r, type_):
    size = ima.size
    r2 = int(min(size[0], size[1]))

    if int(r) <= int(r2 / 2):
        r3 = int(r)
    else:
        r3 = int(r2 / 2)
    if size[0] != size[1]:
        ima = image_view_mode_1(ima, int(r2), int(r2))
    if type_.lower() == 'jpg' or type_.lower() == 'jpeg':
        imb = Image.new('RGBA', (r3 * 2, r3 * 2), (255, 255, 255, 0))
        pima = ima.load()  # 像素的访问对象
        pimb = imb.load()
        r = float(r2 / 2)  # 圆心横坐标

        for i in range(r2):
            for j in range(r2):
                lx = abs(i - r)  # 到圆心距离的横坐标
                ly = abs(j - r)  # 到圆心距离的纵坐标
                l = (pow(lx, 2) + pow(ly, 2)) ** 0.5  # 三角函数 半径

                if l < r3:
                    pimb[i - (r - r3), j - (r - r3)] = pima[i, j]
        imb = imb.convert('RGB')
        return imb
    else:
        circle = Image.new('L', (r3 * 2, r3 * 2), 0)
        ima = ima.convert("RGBA")
        w, h = ima.size

        # draw = ImageDraw.Draw(circle, [(int(w/2-r3), int(h/2-r3))])
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, r3 * 2, r3 * 2), fill=255)

        imb = Image.new('L', ima.size, 255)
        imb.paste(circle, (int(w/2-r3), int(h/2-r3)))
        ima.putalpha(imb)
        crop = str(r3 * 2) + 'x' + str(r3 * 2) + 'a' + str(int((r2 - (r3 * 2)) /2)) + 'a' + str(int((r2 - (r3 * 2)) /2))
        ima = image_mogr_crop(ima, '', crop)
        return ima


def get_box(size, point, width, height, dx=0, dy=0):
    """
    先趋于中心，后偏移。但是始终在原图范围内
    :param size: 数组size[0]底层背景的宽，size[1]底层背景的高
    :param point: 中心圆点坐标，左上角为0,0，右下角为size[0],size[1]
    :param width: 绿色图层的宽
    :param height: 绿色图层的高
    :param dx: 向右偏移量
    :param dy: 向下偏移量
    :return:
    """
    width = min(size[0], width)
    height = min(size[1], height)
    box = [int(point[0] - width / 2), int(point[1] - height / 2), int(point[0] + width / 2), int(point[1] + height / 2)]
    if box[0] < 0:
        # 先给box[2]赋值，它依赖于box[0]
        box[2] -= box[0]
        box[0] = 0
    if box[1] < 0:
        box[3] -= box[1]
        box[1] = 0

    # 因为width和height永远小于等于外层box的宽和高，上下两种情况不会同时出现
    # box[0] < 0 和 box[2] > size[0]不会同时存在
    if box[2] > size[0]:
        box[0] -= (box[2] - size[0])
        box[2] = size[0]
    if box[3] > size[1]:
        box[1] -= (box[3] - size[1])
        box[3] = size[1]

    # 首先判断偏移后是否超出原图范围，如果超出则尽最大可能偏移。保证截图仍在原图内
    if box[2] + dx > size[0]:
        box[0] += (size[0] - box[2])
        box[2] = size[0]
    else:
        box[0] += dx
        box[2] += dx

    if box[3] + dy > size[1]:
        box[1] += (size[1] - box[3])
        box[3] = size[1]
    else:
        box[1] += dy
        box[3] += dy

    return tuple(box)


def _get_gravity_point(size, gravity):
    point = [0, 0]
    if "northwest" == gravity:
        point[0] = 0
        point[1] = 0
    elif "north" == gravity:
        point[0] = int(size[0] / 3)
        point[1] = 0
    elif "northeast" == gravity:
        point[0] = int(2 * (size[0] / 3))
        point[1] = 0
    elif "west" == gravity:
        point[0] = 0
        point[1] = int(size[1] / 3)
    elif "center" == gravity:
        point[0] = int(size[0] / 3)
        point[1] = int(size[1] / 3)
    elif "east" == gravity:
        point[0] = int(2 * (size[0] / 3))
        point[1] = int(size[1] / 3)
    elif "southwest" == gravity:
        point[0] = 0
        point[1] = int(2 * (size[1] / 3))
    elif "south" == gravity:
        point[0] = int(size[0] / 3)
        point[1] = int(2 * (size[1] / 3))
    elif "southeast" == gravity:
        point[0] = int(2 * (size[0] / 3))
        point[1] = int(2 * (size[1] / 3))

    return point


def image_mogr_crop(im, gravity, crop):
    """
    图片裁剪
    """
    size = im.size
    if gravity:
        gravity = gravity.lower()
    point = _get_gravity_point(size, gravity)

    if re.match(r"^([1-9][0-9]*)x$", crop):
        width = int(crop[:-1])
        if width >= 10000:
            return im

        box = get_box(size, point, width, size[1])
        im = im.crop(box)

    elif re.match(r"^x([1-9][0-9]*)$", crop):
        height = int(crop[1:])
        if height >= 10000:
            return im

        box = get_box(size, point, size[0], height)
        im = im.crop(box)

    elif re.match(r"^([1-9][0-9]*)x([1-9][0-9]*)$", crop):
        crop = [int(x) for x in crop.split("x")]
        if min(crop) >= 10000:
            return im

        box = get_box(size, point, crop[0], crop[1])
        im = im.crop(box)

    # elif re.match(r"^([1-9][0-9]*)x([1-9][0-9]*)a([1-9][0-9]*)a([1-9][0-9]*)$", crop):
    elif re.match(r"^([1-9][0-9]*)x([1-9][0-9]*)a([0-9][0-9]*)a([0-9][0-9]*)$", crop):
        # /crop/{cropSize}a<dx>a<dy>
        # 相对于偏移锚点，向右偏移dx个像素，同时向下偏移dy个像素。
        crop = [int(x) for x in re.findall(r"[0-9][0-9]*", crop)]
        if min(crop[:2]) >= 10000:
            return im

        # point[0] += crop[2]
        # point[1] += crop[3]
        box = get_box(size, point, crop[0], crop[1], crop[2], crop[3])
        im = im.crop(box)
    return im


def image_mogr_auto_orient(im):
    """
    根据原图EXIF信息自动旋正，便于后续处理建议放在首位。
      1        2       3      4         5            6           7          8
    888888  888888      88  88      8888888888  88                  88  8888888888
    88          88      88  88      88  88      88  88          88  88      88  88
    8888      8888    8888  8888    88          8888888888  8888888888          88
    88          88      88  88
    88          88  888888  888888
    :rtype : Image
    :param im:
    """
    print('auto_orient on')
    try:
        exif = im._getexif()
    except:
        return im
    if exif and exif.get(0x0112, None):
        orientation = exif.get(0x0112, None)
        if orientation == 1:
            pass
        elif orientation == 2:
            im = im.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 3:
            im = im.transpose(Image.ROTATE_180)
        elif orientation == 4:
            im = im.transpose(Image.FLIP_TOP_BOTTOM)
        elif orientation == 5:
            im = im.transpose(Image.ROTATE_270).transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 6:
            im = im.transpose(Image.ROTATE_270)
        elif orientation == 7:
            im = im.transpose(Image.ROTATE_90).transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 8:
            im = im.transpose(Image.ROTATE_90)

        # 重新修正Orientation值
        # im['Orientation'] = 1

    return im


def file_to_binary(p, type_=None):
    if not type_:
        suffix = re.findall(r'\.[^.\\/:*?"<>|\r\n]+$', p)[0][1:]
        type_ = suffix.lower()
    if 'Range' in request.headers:
        start, end = get_range(request)
        response = partial_response(p, start, end)
    else:
        response = make_response(send_file(p, conditional=True))
    response.headers['Content-Type'] = 'image' + '/' + str(type_)
    response.headers['Content-Disposition'] = 'inline'
    response.headers['Accept-Ranges'] = 'bytes'
    response.cache_control.max_age = 86400
    response.cache_control.public = True
    return response


def partial_response(path, start, end=None):
    file_size = os.path.getsize(path)

    if end is None:
        end = file_size - start - 1
    end = min(end, file_size - 1)
    length = end - start + 1
    with open(path, 'rb') as fd:
        fd.seek(start)
        bytes = fd.read(length)

    response = Response(
        bytes,
        206,  # Partial Content
        mimetype=mimetypes.guess_type(path)[0],  # Content-Type must be correct
        direct_passthrough=True,  # Identity encoding
    )
    response.headers.add(
        'Content-Range', 'bytes {0}-{1}/{2}'.format(
            start, end, file_size,
        ),
    )
    return response


def get_range(request):
    range = request.headers.get('Range')
    m = re.match('bytes=(?P<start>\d+)-(?P<end>\d+)?', range)
    if m:
        start = m.group('start')
        end = m.group('end')
        start = int(start)
        if end is not None:
            end = int(end)
        return start, end
    else:
        return 0, None


# 处理缩略图
def thumbnail_do(file_name, size_w, size_h):
    key = os.getcwd() + '/' + file_name
    im = Image.open(key)
    im.thumbnail((size_w, size_h))
    file_k = os.getcwd() + '/' + 'thumbnail_' + file_name
    im.save(file_k, im.format)
    file_name = 'thumbnail_' + file_name
    return file_name


def resize_do(file_name, size_w, size_h):
    key = os.getcwd() + '/' + file_name
    im = Image.open(key)
    re = im.resize((size_w, size_h))
    file_k = os.getcwd() + '/' + 'thumbnail1_' + file_name
    re.save(file_k, im.format)
    return file_k


# 处理裁剪图片
def crop_do(file_name, left, top, right, bottom):
    key = os.getcwd() + '/' + file_name
    im = Image.open(key)
    cr = im.crop((left, top, right, bottom))
    file_k = os.getcwd() + '/' + 'crop2_' + file_name
    cr.save(file_k, im.format)
    file_name = 'crop2_' + file_name
    return file_name


# 处理格式转换
def convert_do(file_name, type_):
    if type_ == 'jpg':
        type_ = 'jpeg'
    key = os.getcwd() + '/' + file_name
    im = Image.open(key)
    suffix = re.findall(r'\.[^.\\/:*?"<>|\r\n]+$', file_name)[0][1:]
    file_k = os.getcwd() + '/' + 'convert3_' + file_name.split(suffix)[0] + type_
    im.save(file_k, type_)
    file_name = 'convert3_' + file_name.split(suffix)[0] + type_
    return file_name


def toheic(filename):
    i = pyvips.Image.new_from_file(filename)
    suffix = re.findall(r'\.[^.\\/:*?"<>|\r\n]+$', filename)[0][1:]
    file_name = filename.split(suffix)[0] + 'heic'
    i.write_to_file(file_name)
    return file_name


def download_blob(bucket_name, source_blob_name):
    """Downloads a blob from the bucket."""
    file_name = re.split('/', source_blob_name)[-1]
    destination_file_name = os.getcwd() + '/' + file_name
    storage_client = storage.Client()
    bucket = storage_client.get_bucket(bucket_name)
    blob = bucket.blob(source_blob_name)

    blob.download_to_filename(destination_file_name)
    print('Blob {} downloaded to {}.'.format(
        source_blob_name,
        destination_file_name))


@app.route('/', methods=["GET", "POST"])
def hello():
    return 'index'


class RegexConverter(BaseConverter):
    def __init__(self, url_map, *args):
        super(RegexConverter, self).__init__(url_map)
        self.regex = args[0]


app.url_map.converters['re'] = RegexConverter


@app.route('/<re(r"[\w\W]*"):route_file>', methods=['GET', 'POST'])
def image2(route_file):
    request_file = re.split('/', route_file)[-1]
    request_action = request.args.get("x-oss-process")
    bucket_name = os.getenv('bucket_name')
    # try:
    #     download_blob(bucket_name, route_file)
    # except:
    #     return 'downloadFail'
    suffix = re.findall(r'\.[^.\\/:*?"<>|\r\n]+$', request_file)[0][1:]
    type_ = suffix
    quality = 75
    if not request_action:
        return file_to_binary(request_file, suffix)

    key = os.getcwd() + '/' + request_file
    if type_.lower() == 'gif':
        gif = Image.open(key)
        dura = gif.info['duration']
        imgs = [f.copy() for f in ImageSequence.Iterator(gif)]

        index = 0
        imglist = []
        os.mkdir("imagesttt")
        for frame in imgs:
            frame.save("./imagesttt/%d.png" % index)
            im = Image.open("./imagesttt/%d.png" % index)


            if re.findall('auto-orient', request_action):
                im = image_mogr_auto_orient(im)
            req = request_action.split('/')
            if req[0] == 'image':
                for i in req:
                    if re.findall('format', i):
                        act_for = request_action.split('/')[req.index(i)]
                        act_for = act_for.split(',')
                        if act_for[0] == 'format':
                            type_ = act_for[1]
                            if type_.lower() == 'jpg':
                                type_ = 'jpeg'
                            # if type_.lower() == 'heic':
                            #     filename = toheic(request_file)
                            #     file_k = os.getcwd() + '/' + filename
                            #     return file_to_binary(file_k, 'heic')
                for i in req:
                    if re.findall('quality', i):
                        act_for = request_action.split('/')[req.index(i)]
                        act_for = act_for.split(',')
                        if act_for[0] == 'quality':
                            quality = act_for[1].split('_')[1]
                    if re.findall('_', i):
                        act_1 = req[req.index(i)]
                        act_2 = act_1.split(',')
                        if act_2[0] == 'crop':
                            act_2.pop(0)
                            act_d = {}
                            for i in act_2:
                                act_d[i.split('_')[0]] = i.split('_')[1]
                            w = act_d.get('w', str(im.size[0]))
                            h = act_d.get('h', str(im.size[1]))
                            x = act_d.get('x', '0')
                            y = act_d.get('y', '0')
                            if int(im.size[0]) < (int(w) + int(x)):
                                w = str(int(im.size[0]) - int(x))
                            if int(im.size[1]) < (int(h) + int(y)):
                                h = str(int(im.size[1]) - int(y))
                            crop = w + 'x' + h + 'a' + x + 'a' + y
                            g = act_d.get('g')
                            if g:
                                if g == 'nw':
                                    g = 'northwest'
                                elif g == 'ne':
                                    g = 'northeast'
                                elif g == 'sw':
                                    g = 'southwest'
                                elif g == 'se':
                                    g = 'southeast'
                            im = image_mogr_crop(im, g, crop)

                        elif act_2[0] == 'resize':
                            act_2.pop(0)
                            act_d = {}
                            for i in act_2:
                                act_d[i.split('_')[0]] = i.split('_')[1]
                            m = act_d.get('m')
                            if not m:
                                m = 'lfit'
                            w = act_d.get('w', im.size[0])
                            h = act_d.get('h', im.size[1])
                            l = act_d.get('l')
                            s = act_d.get('s')
                            p = act_d.get('p')
                            if l:
                                im = image_view_mode_2(im, l, l)
                            if s:
                                im = image_view_mode_3(im, s, s)
                            if m == 'lfit':
                                im = image_view_mode_2(im, w, h)
                            elif m == 'mfit':
                                im = image_view_mode_3(im, w, h)
                            elif m == 'fill':
                                im = image_view_mode_1(im, w, h)
                            elif m == 'fixed':
                                im = im.resize((int(w), int(h)))
                            else:
                                return 'm err'
                            if p:
                                w = im.size[0] * (int(p) / 100)
                                h = im.size[1] * (int(p) / 100)
                                im = image_view_mode_2(im, w, h)
                        elif act_2[0] == 'circle':
                            act_2.pop(0)
                            act_d = {}
                            for i in act_2:
                                act_d[i.split('_')[0]] = i.split('_')[1]
                            r = act_d.get('r')
                            if not r:
                                return 'r err'
                            im = image_view_mode_6(im, r, type_)

            imglist.append(im)
            index += 1

        file_k = os.getcwd() + '/' + str(request_action).replace('/', '') + '_' + request_file
        os.system("rm -rf ./imagesttt")
        if quality != 75:
            imglist[0].save(file_k, type_, save_all=True, append_images=imglist[1:], loop=0, duration=dura, quality=int(quality))
        else:
            imglist[0].save(file_k, type_, save_all=True, append_images=imglist[1:], loop=0, duration=dura)
        return file_to_binary(file_k, type_)


    im = Image.open(request_file)
    if re.findall('auto-orient', request_action):
        im = image_mogr_auto_orient(im)
    req = request_action.split('/')
    if req[0] == 'image':
        for i in req:
            if re.findall('format', i):
                act_for = request_action.split('/')[req.index(i)]
                act_for = act_for.split(',')
                if act_for[0] == 'format':
                    type_ = act_for[1]
                    if type_.lower() == 'jpg':
                        type_ = 'jpeg'
                    # if type_.lower() == 'heic':
                    #     filename = toheic(request_file)
                    #     file_k = os.getcwd() + '/' + filename
                    #     return file_to_binary(file_k, 'heic')
        for i in req:
            if re.findall('quality', i):
                act_for = request_action.split('/')[req.index(i)]
                act_for = act_for.split(',')
                if act_for[0] == 'quality':
                    quality = act_for[1].split('_')[1]
            if re.findall('_', i):
                act_1 = req[req.index(i)]
                act_2 = act_1.split(',')
                if act_2[0] == 'crop':
                    act_2.pop(0)
                    act_d = {}
                    for i in act_2:
                        act_d[i.split('_')[0]] = i.split('_')[1]
                    w = act_d.get('w', str(im.size[0]))
                    h = act_d.get('h', str(im.size[1]))
                    x = act_d.get('x', '0')
                    y = act_d.get('y', '0')
                    if int(im.size[0]) < (int(w) + int(x)):
                        w = str(int(im.size[0]) - int(x))
                    if int(im.size[1]) < (int(h) + int(y)):
                        h = str(int(im.size[1]) - int(y))
                    crop = w + 'x' + h + 'a' + x + 'a' + y
                    g = act_d.get('g')
                    if g:
                        if g == 'nw':
                            g = 'northwest'
                        elif g == 'ne':
                            g = 'northeast'
                        elif g == 'sw':
                            g = 'southwest'
                        elif g == 'se':
                            g = 'southeast'
                    im = image_mogr_crop(im, g, crop)

                elif act_2[0] == 'resize':
                    act_2.pop(0)
                    act_d = {}
                    for i in act_2:
                        act_d[i.split('_')[0]] = i.split('_')[1]
                    m = act_d.get('m')
                    if not m:
                        m = 'lfit'
                    w = act_d.get('w', im.size[0])
                    h = act_d.get('h', im.size[1])
                    l = act_d.get('l')
                    s = act_d.get('s')
                    p = act_d.get('p')
                    if l:
                        im = image_view_mode_2(im, l, l)
                    if s:
                        im = image_view_mode_3(im, s, s)
                    if m == 'lfit':
                        im = image_view_mode_2(im, w, h)
                    elif m == 'mfit':
                        im = image_view_mode_3(im, w, h)
                    elif m == 'fill':
                        im = image_view_mode_1(im, w, h)
                    elif m == 'fixed':
                        im = im.resize((int(w), int(h)))
                    else:
                        return 'm err'
                    if p:
                        w = im.size[0] * (int(p) / 100)
                        h = im.size[1] * (int(p) / 100)
                        im = image_view_mode_2(im, w, h)
                elif act_2[0] == 'circle':
                    act_2.pop(0)
                    act_d = {}
                    for i in act_2:
                        act_d[i.split('_')[0]] = i.split('_')[1]
                    r = act_d.get('r')
                    if not r:
                        return 'r err'
                    im = image_view_mode_6(im, r, type_)

    file_k = os.getcwd() + '/' + str(request_action).replace('/', '') + '_' + request_file
    if type_.lower() == 'jpg':
        type_ = 'jpeg'
    if suffix.lower() == 'jpg':
        suffix = 'jpeg'
    if type_.lower() == 'heic' or type_.lower() == 'heif':
        im.save(file_k, suffix)
        filename = toheic(file_k)
        return file_to_binary(filename, 'heic')
    if quality != 75:
        im.save(file_k, type_, quality=int(quality))
    else:
        im.save(file_k, type_)
    return file_to_binary(file_k, type_)

    # if request_action == 'thumbnail':
    #     size_w = request.args.get('size_w')
    #     size_h = request.args.get('size_h')
    #     if request_file and size_w and size_h:
    #         download_blob(bucket_name, request_file)
    #         request_file = resize_do(request_file, int(size_w), int(size_h))
    #     else:
    #         raise 'miss parameter1'
    # elif request_action == 'crop':
    #     left = request.args.get('left')
    #     top = request.args.get('top')
    #     right = request.args.get('right')
    #     bottom = request.args.get('bottom')
    #     if request_file and left and top and right and bottom:
    #         download_blob(bucket_name, request_file)
    #         request_file = crop_do(request_file, int(left), int(top), int(right), int(bottom))
    #     else:
    #         raise 'miss parameter2'
    # elif request_action == 'convert':
    #     type_ = request.args.get("type")
    #     if type_ and request_file:
    #         download_blob(bucket_name, request_file)
    #         if type_ == 'heic':
    #             request_file = toheic(request_file)
    #         else:
    #             request_file = convert_do(request_file, type_)
    # try:
    #     return file_to_binary(request_file, type_)
    # except:
    #     return 'miss parameter'


if __name__ == '__main__':
    # HOST = '0.0.0.0'
    # PORT = 8080
    # app.run(HOST, PORT, debug=True)
    app = Flask(__name__)
    app.run()
