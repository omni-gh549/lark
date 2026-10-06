FROM debian:12-slim
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv nodejs npm git curl wget ca-certificates \
      build-essential jq ripgrep unzip zip less nano vim-tiny procps sqlite3 \
      imagemagick ffmpeg pandoc chromium fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf python3 /usr/bin/python
ENV PIP_BREAK_SYSTEM_PACKAGES=1
# the Playwright driver only; the browser is Debian's chromium
RUN pip install --no-cache-dir playwright
COPY agent.py /opt/agent.py
WORKDIR /home/lark
CMD ["python3", "/opt/agent.py"]
