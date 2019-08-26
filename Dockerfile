FROM alpine:3.5

LABEL maintainer="docker@doowan.net"

RUN apk -Uuv add bash \
                 curl \
                 curl-dev \
                 gcc \
                 libffi-dev \
                 libmagic \
                 linux-headers \
                 libressl-dev \
                 musl-dev \
                 python \
                 python-dev \
                 py-curl \
                 py-openssl \
                 py-pip && \
    find /var/cache/apk/ -type f -delete

RUN pip install autond

ADD docker-run.sh /run.sh

EXPOSE 8666/tcp

CMD ["/run.sh"]
