FROM python:3.6.9

WORKDIR /relay-tunnel

ADD relay_tunnel relay_tunnel
ADD setup.py setup.py
ADD requirements.txt requirements.txt
ADD README.md README.md
ADD docker/relay-server.sh /usr/bin/relay-server.sh

RUN apt update \
    && apt install -y nginx \
    && chmod 755 /usr/bin/relay-server.sh \
    && pip3 install -e . # -i https://mirrors.tencent.com/pypi/simple/

ADD docker/relay.conf /etc/nginx/conf.d/

ENTRYPOINT ["/usr/bin/relay-server.sh"]

EXPOSE 80
