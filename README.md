# RelayTunnel

用于建立中继隧道的TurboTunnel插件。

## 功能特性

* WebSocket Relay Tunnel
* HTTP(S) Relay Tunnel
* IRC Relay Tunnel（开发中）

## 使用方法

### 安装方法

```bash
$ pip3 install relay-tunnel
```

### WebSocket Relay Tunnel

* 中继服务端

```bash
$ turbo-tunnel -l ws+relay://0.0.0.0:8080/relay/ -p relay_tunnel
```

也可以使用Docker方式来运行：

```bash
$ sudo docker build -t relay-server -f docker/Dockerfile .
$ sudo docker run -it -p 8080:80 relay-server
```

* 中继节点

```bash
$ relay-tunnel -s "ws://10.0.0.1:8080/relay/?client_id=${node_id}"
```

其中`${node_id}`是中继节点的ID，可以为任意字符串，但必须保持唯一。

* 客户端

```bash
$ turbo-tunnel -l tcp://127.0.0.1:7777 -t "ws+relay://10.0.0.1:8080/relay/?client_id=${node_id}&target_id=${target_id}" -t tcp://private.com:80
```

这条命令表示将中继节点所在网络中的`private.com:80`服务映射到本地的`127.0.0.1:7777`地址。



