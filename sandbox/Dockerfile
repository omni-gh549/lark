FROM debian:12-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv nodejs npm git curl wget ca-certificates \
      build-essential jq ripgrep unzip zip less nano vim-tiny procps sqlite3 \
      imagemagick ffmpeg pandoc chromium fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf python3 /usr/bin/python
ENV PIP_BREAK_SYSTEM_PACKAGES=1
# Playwright drives the local browser. Patchright drives the home browser, whose own
# screen and network are what shop sign-in checks accept.
RUN pip install --no-cache-dir playwright patchright
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*
COPY agent.py /opt/agent.py
WORKDIR /home/lark
CMD ["python3", "/opt/agent.py"]
