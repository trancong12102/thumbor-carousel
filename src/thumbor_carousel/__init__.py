import base64
import struct

import cv2
import numpy
import tornado
from PIL import Image
from thumbor.filters import BaseFilter, filter_method
from thumbor.loaders import LoaderResult
from thumbor.utils import logger


class Filter(BaseFilter):
    """
        Filter for creating a carousel from multiple images.

        Usage:
            /filters:carousel(
                <urls_base64>,
                <img_count>,
                <img_height>,
                <img_spacing>,
                <background_color>
                <more_text_color>
            )
    """

    def __init__(self, params, context=None):
        super().__init__(params, context)
        self.storage = self.context.modules.storage

    @staticmethod
    def stretch(engine, height: int):
        width, _ = engine.size
        new_width = int((width / engine.size[1]) * height)
        engine.resize(new_width, height)

    def padding(self, engine, padding_x: int, padding_y: int, color: str):
        offset_x = padding_x
        offset_y = padding_y

        new_width = engine.size[0] + (2 * padding_x)
        new_height = engine.size[1] + (2 * padding_y)

        new_engine = self.context.modules.engine.__class__(self.context)
        new_engine.image = new_engine.gen_image((new_width, new_height), "#" + color)
        new_engine.enable_alpha()
        new_engine.paste(engine, (offset_x, offset_y))

        engine.image = new_engine.image

    def text(self, height: int, text: str, color: str):
        color_tuple = struct.unpack('BBB', bytes.fromhex(color.lstrip('#')))
        font = cv2.QT_FONT_NORMAL
        font_scale = 0
        thickness = 1

        while True:
            font_scale += 0.1
            text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
            if text_size[0] >= height // 1.5:
                break

        width = text_size[0]
        image = numpy.zeros((height, width, 4), numpy.uint8)
        text_x = 0
        text_y = (height + text_size[1]) // 2
        cv2.putText(
            image,
            text,
            (text_x, text_y),
            font,
            font_scale,
            color_tuple + (255,),
            thickness,
            cv2.LINE_AA,
        )

        engine = self.context.modules.engine.__class__(self.context)
        engine.image = Image.fromarray(image)

        return engine, width

    def add_more_text(self, engine, count: int, height: int, spacing: int, color: str, background_color: str):
        more_text = f"+{count}"
        more_text_engine, more_text_width = self.text(height, more_text, color)

        new_engine = self.context.modules.engine.__class__(self.context)
        new_engine.image = new_engine.gen_image((engine.size[0] + more_text_width + spacing, height), background_color)

        new_engine.paste(engine, (0, 0))
        new_engine.paste(more_text_engine, (engine.size[0] + spacing, 0))

        return new_engine

    def join(self, engines, spacing: int, background_color: str):
        height = engines[0].size[1]
        width = 0
        for idx, engine in enumerate(engines):
            width += engine.size[0] + (spacing if idx < len(engines) - 1 else 0)

        new_engine = self.context.modules.engine.__class__(self.context)
        new_engine.image = new_engine.gen_image((width, height), background_color)

        offset_x = 0
        for idx, engine in enumerate(engines):
            new_engine.paste(engine, (offset_x, 0))
            offset_x += engine.size[0] + (spacing if idx < len(engines) - 1 else 0)

        return new_engine

    async def load_images(self, urls_base64: str):
        images = []

        urls = base64.b64decode(urls_base64).decode('utf-8').split(',')
        if len(urls) <= 0:
            raise tornado.web.HTTPError(400, "No images provided")

        for url in urls:
            if not self.validate(url):
                raise tornado.web.HTTPError(400)

            buffer = await self.storage.get(url)
            if buffer is not None:
                images.append(buffer)
                continue

            result = await self.context.modules.loader.load(
                self.context, url
            )

            if isinstance(result, LoaderResult) and not result.successful:
                logger.warning(
                    "bad image result error=%s metadata=%s",
                    result.error,
                    result.metadata,
                )
                raise tornado.web.HTTPError(
                    400,
                    "bad image result error=%s metadata=%s".format()
                )

            if isinstance(result, LoaderResult):
                buffer = result.buffer
            else:
                buffer = result

            await self.storage.put(url, buffer)
            await self.storage.put_crypto(url)

            images.append(buffer)

        engines = []

        for image in images:
            engine = self.context.modules.engine.__class__(self.context)
            engine.load(image, None)
            engine.enable_alpha()
            engines.append(engine)

        return engines

    def validate(self, url):
        if not hasattr(self.context.modules.loader, "validate"):
            return True

        if not self.context.modules.loader.validate(self.context, url):
            logger.warning('image source not allowed: "%s"', url)
            return False
        return True

    @filter_method(
        BaseFilter.String,  # urls_base64 (base64 encoded string of comma separated image urls)
        BaseFilter.PositiveNonZeroNumber,  # img_count (number of images in carousel)
        BaseFilter.PositiveNonZeroNumber,  # img_height (height of images)
        BaseFilter.PositiveNonZeroNumber,  # img_spacing (spacing between images)
        BaseFilter.String,  # background_color (color of background)
        BaseFilter.String  # more_text_color (color of more text)
    )
    async def carousel(
            self,
            urls_base64: str,
            img_count: int = 1,
            img_height: int = 100,
            img_spacing: int = 10,
            background_color: str = 'ffffff',
            more_text_color: str = '000000'
    ):
        image_engines = await self.load_images(urls_base64)

        for engine in image_engines:
            self.stretch(engine, img_height)

        carousel_engine = self.join(image_engines, img_spacing, '#' + background_color)

        if len(image_engines) > img_count:
            carousel_engine = self.add_more_text(
                carousel_engine,
                len(image_engines) - img_count,
                img_height,
                img_spacing,
                '#' + more_text_color,
                '#' + background_color,
            )

        self.engine.image = carousel_engine.image
        self.context.request.format = 'jpeg'
