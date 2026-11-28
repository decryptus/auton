FROM python:3.12-slim-bookworm AS build

# Only the files needed to build the autond wheel enter this stage.
WORKDIR /src
COPY setup.py setup.cfg setup.yml pyproject.toml MANIFEST.in README.md LICENSE VERSION RELEASE requirements-auton.txt requirements-autond.txt ./
COPY bin/ bin/
COPY auton/ auton/
COPY auton_client/ auton_client/
RUN AUTON_PACKAGE=autond pip wheel --no-cache-dir --wheel-dir /wheels '.[auth,oidc,redis]'

FROM python:3.12-slim-bookworm AS runtime

LABEL maintainer="docker@doowan.net"

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash curl libmagic1 libcurl4 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system auton \
    && useradd --system --gid auton --home-dir /etc/auton auton \
    && mkdir -p /run/auton /var/log/autond /etc/auton /var/lib/autond/auth /var/lib/autond/jobs \
    && chown -R auton:auton /run/auton /var/log/autond /etc/auton /var/lib/autond

# The runtime image holds the installed package and its configuration only,
# never the repository sources, tests or maintainer files.
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels 'autond[auth,oidc,redis]' \
    && rm -rf /wheels
COPY docker-run.sh /run.sh
COPY etc/auton/imports /etc/auton/imports
COPY etc/auton/modules /etc/auton/modules
COPY etc/auton/auton.yml.example /etc/auton/auton.yml
RUN chmod +x /run.sh

WORKDIR /etc/auton
USER auton
EXPOSE 8666/tcp
CMD ["/run.sh"]

# Test-only stage, never published: the runtime image plus the repository
# sources and client dependencies needed by the combined integration suite.
FROM runtime AS test
USER root
WORKDIR /opt/auton
COPY . .
RUN pip install --no-cache-dir -r requirements-auton.txt
USER auton

FROM runtime AS final
