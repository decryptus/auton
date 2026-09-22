FROM python:3.12-slim-bookworm

LABEL maintainer="docker@doowan.net"

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash curl libmagic1 libcurl4 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system auton \
    && useradd --system --gid auton --home-dir /etc/auton auton \
    && mkdir -p /run/auton /var/log/autond /etc/auton \
    && chown -R auton:auton /run/auton /var/log/autond /etc/auton

WORKDIR /opt/auton
COPY . .
RUN AUTON_PACKAGE=autond pip install --no-cache-dir .
COPY docker-run.sh /run.sh
COPY etc/auton/modules /etc/auton/modules
COPY etc/auton/auton.yml.example /etc/auton/auton.yml
RUN chmod +x /run.sh

USER auton
EXPOSE 8666/tcp
CMD ["/run.sh"]
