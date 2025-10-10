# 视频流插件 (UDP接收 + WebSocket分发)

## 概述
视频流插件专门负责处理摄像头视频流和AI检测框数据的接收与分发。采用**UDP接收**（高效）+ **WebSocket分发**（灵活）的混合架构。

**设计理念**：视频流是单向传输，使用UDP减少开销；客户端订阅使用WebSocket保证连接管理。

## 核心功能

### 1. UDP视频帧接收
- 监听UDP端口接收视频帧（二进制格式）
- 解析摄像头帧头部信息
- 缓存最新帧用于新订阅者
- 发布camera.frame事件

### 2. UDP检测框接收
- 监听UDP端口接收检测框数据（JSON格式）
- 转发检测框给对应摄像头的订阅者
- 发布detection.boxes事件

### 3. WebSocket订阅服务
- 客户端通过WebSocket连接订阅摄像头
- 支持多客户端同时订阅同一摄像头
- 订阅管理：subscribe_camera / unsubscribe_camera
- 自动清理断开的订阅者

### 4. 异步帧分发（核心优化）
- **每个订阅者独立的发送协程**：不互相阻塞
- **帧队列管理**：只保留最新1帧，自动丢弃旧帧
- **流量控制**：限制发送速率（默认20fps）
- **过期帧跳过**：超过1秒的帧自动丢弃
- **性能监控**：实时统计丢帧率、吞吐量

## 架构设计

```
┌─────────────────┐
│  Jetson设备     │
│  (摄像头+AI)    │
└────────┬────────┘
         │ UDP单向发送
         │ (视频帧 + 检测框)
         ↓
┌─────────────────────────────┐
│   VideoStream插件           │
│                             │
│  ┌──────────┐  ┌─────────┐ │
│  │UDP接收器 │  │视频帧   │ │
│  │14000/14001│→│处理器   │ │
│  └──────────┘  └────┬────┘ │
│                     │       │
│  ┌──────────────────↓─────┐│
│  │  VideoFrameHandler     ││
│  │  (异步发送 + 丢帧策略) ││
│  └──────────┬──────────────┘│
│             │                │
│  ┌──────────↓──────────────┐│
│  │ WebSocket订阅服务(14002)││
│  └──────────┬──────────────┘│
└─────────────┼────────────────┘
              │ WebSocket
              ↓
     ┌────────┴────────┐
     │                 │
┌────↓─────┐    ┌─────↓────┐
│ 前端客户端│    │其他订阅者│
└──────────┘    └──────────┘
```

## 为什么视频流使用UDP？

| 特性 | UDP | WebSocket |
|------|-----|-----------|
| 传输效率 | **高**（无握手） | 低（TCP开销） |
| 实时性 | **强**（不等待重传） | 弱（重传延迟） |
| 丢包处理 | 允许丢帧 | 必须重传 |
| 适用场景 | **视频流** | 指令控制 |
| 网络占用 | 小 | 大 |

**结论**：视频流是单向、允许丢帧的，UDP是最佳选择。

## 协议格式

### UDP视频帧格式（二进制）

```
+--------+------------+-----------+----------+-----------+----------+
| Magic  | Camera ID  | Camera ID | Frame    | Timestamp | JPEG     |
| (4B)   | Length(4B) | (变长)    | Seq (4B) | (8B)      | Data     |
+--------+------------+-----------+----------+-----------+----------+
0x43414D46 (CAMF)
```

**解析示例**：
```python
magic = struct.unpack('>I', data[0:4])[0]  # 0x43414D46
camera_id_len = struct.unpack('>I', data[4:8])[0]
camera_id = data[8:8+camera_id_len].decode('utf-8')
offset = 8 + camera_id_len
frame_seq = struct.unpack('>I', data[offset:offset+4])[0]
timestamp = struct.unpack('>d', data[offset+4:offset+12])[0]
jpeg_data = data[offset+12:]
```

### UDP检测框格式（JSON）

```json
{
    "camera_id": "camera_0",
    "id": "frame_12345",
    "ts": 1696500000.123,
    "det": [
        {
            "class": "person",
            "confidence": 0.95,
            "bbox": [100, 200, 150, 250]
        }
    ],
    "scale": 1.0,
    "w": 1920,
    "h": 1080
}
```

### WebSocket订阅消息

**订阅摄像头**：
```json
{
    "type": "subscribe_camera",
    "camera_id": "camera_0"
}
```

**取消订阅**：
```json
{
    "type": "unsubscribe_camera",
    "camera_id": "camera_0"
}
```

**订阅确认**：
```json
{
    "type": "subscribe_ack",
    "camera_id": "camera_0",
    "status": "success",
    "timestamp": 1696500000.0
}
```

## 发布事件

### camera.frame.{camera_id}
每收到一帧视频发布一次
```json
{
    "camera_id": "camera_0",
    "frame_seq": 12345,
    "timestamp": 1696500000.123,
    "frame_size": 45678,
    "subscriber_count": 3
}
```

### detection.boxes
检测框数据
```json
{
    "camera_id": "camera_0",
    "detections": [...],
    "frame_id": "frame_12345",
    "timestamp": 1696500000.123,
    "detection_count": 5
}
```

## 接口方法

### get_stream_status()
获取视频流状态

**输出**:
```json
{
    "running": true,
    "udp_video_port": 14000,
    "udp_detection_port": 14001,
    "ws_port": 14002,
    "protocol": "udp+websocket",
    "camera_stream_count": 2,
    "ws_client_count": 5,
    "frame_subscriber_count": 8,
    "statistics": {
        "uptime": 7200.5,
        "frames_received": 144000,
        "frames_forwarded": 720000,
        "detections_received": 12000,
        "errors": 3
    },
    "video_handler_stats": {
        "frames_received": 144000,
        "frames_sent": 720000,
        "frames_dropped": 1200,
        "frames_failed": 50,
        "drop_rate": 0.83,
        "avg_frame_size": 52000,
        "active_subscribers": 5
    }
}
```

### get_performance_report()
获取性能报告

**输出**:
```json
{
    "success": true,
    "performance": {
        "video_processing": {
            "frames_received": 144000,
            "frames_sent": 720000,
            "frames_dropped": 1200,
            "frames_failed": 50,
            "drop_rate_percent": 0.83,
            "avg_frame_size_kb": 50.78,
            "throughput_mbps": 45.6,
            "active_subscribers": 5
        },
        "overall": {
            "uptime_seconds": 7200.5,
            "frames_received": 144000,
            "detections_received": 12000,
            "errors": 3
        }
    },
    "recommendations": [
        {
            "level": "success",
            "message": "性能良好，无需优化"
        }
    ]
}
```

### get_camera_streams()
获取所有摄像头流信息

**输出**:
```json
{
    "success": true,
    "stream_count": 2,
    "streams": {
        "camera_0": {
            "frame_count": 72000,
            "uptime": 3600.0,
            "fps": 20.0,
            "last_frame_seq": 71999,
            "last_frame_size": 52000,
            "last_frame_time": 1696503600.0,
            "subscriber_count": 3
        },
        "camera_1": {
            "frame_count": 72000,
            "uptime": 3600.0,
            "fps": 20.0,
            "last_frame_seq": 71999,
            "last_frame_size": 48000,
            "last_frame_time": 1696503600.0,
            "subscriber_count": 2
        }
    }
}
```

### get_camera_subscribers()
获取摄像头订阅者信息

**输出**:
```json
{
    "success": true,
    "subscribers": {
        "camera_0": {
            "subscriber_count": 3,
            "subscribers": [
                "('192.168.1.100', 54321)",
                "('192.168.1.101', 54322)",
                "('192.168.1.102', 54323)"
            ]
        }
    }
}
```

### get_latest_frame(camera_id)
获取最新帧（用于快照）

**输入**:
```json
{
    "camera_id": "camera_0"
}
```

**输出**:
```json
{
    "success": true,
    "camera_id": "camera_0",
    "frame_data": "base64_encoded_jpeg...",
    "frame_size": 52000
}
```

## 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| udp_video_port | number | 14000 | UDP视频流接收端口 |
| udp_detection_port | number | 14001 | UDP检测框接收端口 |
| ws_port | number | 14002 | WebSocket订阅服务端口 |

## 设备端实现（发送端）

### 1. 发送视频帧（UDP）

```python
import socket
import struct
import time

def send_video_frame(camera_id: str, frame_seq: int, jpeg_data: bytes):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # 构建帧头
    magic = 0x43414D46  # 'CAMF'
    camera_id_bytes = camera_id.encode('utf-8')
    camera_id_len = len(camera_id_bytes)
    timestamp = time.time()
    
    # 打包
    header = struct.pack('>I', magic)
    header += struct.pack('>I', camera_id_len)
    header += camera_id_bytes
    header += struct.pack('>I', frame_seq)
    header += struct.pack('>d', timestamp)
    
    # 发送
    data = header + jpeg_data
    sock.sendto(data, ('server_ip', 14000))
    sock.close()
```

### 2. 发送检测框（UDP）

```python
import socket
import json

def send_detection_boxes(camera_id: str, detections: list):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    message = {
        "camera_id": camera_id,
        "id": f"frame_{frame_seq}",
        "ts": time.time(),
        "det": detections,
        "scale": 1.0,
        "w": 1920,
        "h": 1080
    }
    
    data = json.dumps(message).encode('utf-8')
    sock.sendto(data, ('server_ip', 14001))
    sock.close()
```

## 客户端实现（接收端）

### 订阅摄像头流

```python
import asyncio
import websockets

async def subscribe_camera():
    uri = "ws://server_ip:14002"
    async with websockets.connect(uri) as websocket:
        # 订阅摄像头
        await websocket.send(json.dumps({
            "type": "subscribe_camera",
            "camera_id": "camera_0"
        }))
        
        # 接收视频帧和检测框
        async for message in websocket:
            if isinstance(message, bytes):
                # 视频帧（二进制）
                handle_video_frame(message)
            elif isinstance(message, str):
                # 检测框（JSON）
                data = json.loads(message)
                if data.get('type') == 'detection_data':
                    handle_detection_boxes(data)
```

## 性能优化特性

### 1. 异步发送架构
- 每个订阅者独立的发送协程
- 不会因某个客户端慢而影响其他客户端
- 自动处理发送失败（连续5次失败后断开）

### 2. 丢帧策略
- 队列只保留最新1帧
- 旧帧自动被新帧替换
- 过期帧（>1秒）直接跳过

### 3. 流量控制
- 限制发送帧率（默认20fps）
- 防止网络拥塞
- 可配置目标帧率

### 4. 性能监控
- 实时统计丢帧率
- 监控发送失败率
- 计算吞吐量
- 生成优化建议

## 使用示例

### 1. 启动插件

```python
# 安装插件
plugin_manager.install_plugin("video_stream")

# 启用插件
plugin_manager.enable_plugin("video_stream")
```

### 2. 查看状态

```python
# 获取整体状态
status = plugin_manager.call_plugin_interface(
    "video_stream",
    "get_stream_status"
)
print(f"视频流状态: {status}")

# 获取性能报告
report = plugin_manager.call_plugin_interface(
    "video_stream",
    "get_performance_report"
)
print(f"丢帧率: {report['performance']['video_processing']['drop_rate_percent']}%")
```

### 3. 获取快照

```python
# 获取最新帧
frame = plugin_manager.call_plugin_interface(
    "video_stream",
    "get_latest_frame",
    {"camera_id": "camera_0"}
)

if frame['success']:
    import base64
    jpeg_data = base64.b64decode(frame['frame_data'])
    with open('snapshot.jpg', 'wb') as f:
        f.write(jpeg_data)
```

## 与EventBridge插件的关系

**VideoStream插件** (本插件)：
- **协议**: UDP接收 + WebSocket分发
- **端口**: 14000 (UDP视频)、14001 (UDP检测框)、14002 (WebSocket订阅)
- **功能**: 单向视频流传输
- **数据**: 视频帧、检测框

**EventBridge插件**：
- **协议**: WebSocket
- **端口**: 13000
- **功能**: 双向指令通信
- **数据**: 控制命令、状态上报、传感器数据

**两个插件独立运行，职责清晰！**

## 故障排查

### 1. 没有收到视频帧
- 检查UDP端口是否正确：`netstat -an | grep 14000`
- 检查防火墙是否开放UDP端口
- 确认发送端IP地址正确
- 查看插件日志是否有错误

### 2. 丢帧率过高
- 降低发送端帧率或JPEG质量
- 检查网络带宽是否充足
- 查看性能报告的优化建议
- 考虑增加订阅端处理速度

### 3. WebSocket连接失败
- 确认WebSocket端口14002可访问
- 检查服务器防火墙设置
- 查看插件是否正常运行

### 4. 检测框与视频不同步
- 确认发送端使用相同的frame_id
- 检查时间戳是否准确
- 查看网络延迟情况

## 版本历史

- v1.0.0 (2025-10-10)
  - 从EventBridge插件分离
  - 采用UDP接收 + WebSocket分发架构
  - 实现VideoFrameHandler异步发送
  - 支持丢帧策略和流量控制
  - 添加性能监控和优化建议