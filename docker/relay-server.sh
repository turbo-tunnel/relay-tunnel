#!/usr/bin/env bash

for i in "$@"
do
case $i in
    --org=*)
    ORG="${i#*=}"
    shift # past argument=value
    ;;
    --domain=*)
    DOMAIN="${i#*=}"
    shift # past argument=value
    ;;
    --default)
    DEFAULT=YES
    shift # past argument with no value
    ;;
    *)
          # unknown option
    ;;
esac
done

if [ ! -f "/etc/nginx/ssl/default.key" ]; then
  echo "Create ssl certificate files."
  openssl genrsa -out server.key 2048
  openssl req -new -key server.key -out server.csr -subj "/C=CN/ST=Guangdong/L=Guangzhou/O=${ORG:-drunkdream}/OU=${ORG:-drunkdream}/CN=${DOMAIN:-relay.drunkdream.com}"
  openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt
  mkdir /etc/nginx/ssl
  mv server.key /etc/nginx/ssl/default.key
  mv server.crt /etc/nginx/ssl/default.crt
  rm server.csr
fi

turbo-tunnel -l ws+relay://127.0.0.1:8080/relay/ -p relay_tunnel "$@" &

nginx -g 'daemon off;'
