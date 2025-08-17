"""
CPU温度监测插件
实时监测并打印CPU温度信息
"""

import os
import time
import threading
from pathlib import Path
from app.usv.plugin_base import Plugin, Response


class CpuTemperaturePlugin(Plugin):
    """CPU温度监测插件"""
    
    VERSION = '1.0.0'
    MIN_COMPATIBLE_VERSION = '1.0.0'
    
    def __init__(self, plugin_id: str, plugin_base):
        super().__init__(plugin_id, plugin_base)
        
        # 监测控制
        self._monitoring = False
        self._monitor_thread = None
        self._monitor_interval = 5.0  # 默认5秒监测一次
        
        # 温度传感器路径
        self._thermal_paths = [
            '/sys/class/thermal/thermal_zone0/temp',
            '/sys/class/thermal/thermal_zone1/temp',
            '/sys/class/thermal/thermal_zone2/temp',
            '/sys/class/thermal/thermal_zone3/temp'
        ]
        
        self.log_info("CPU温度监测插件初始化完成")
    
    def _handle_enable(self) -> Response:
        """启用插件时开始监测"""
        try:
            # 获取配置
            self._monitor_interval = self.get_config('monitor_interval', 5.0)
            
            # 检查是否有可用的温度传感器
            available_sensors = self._find_available_sensors()
            if not available_sensors:
                self.log_warning("未找到可用的温度传感器")
                print("⚠️  警告: 未找到可用的温度传感器")
                return Response(success=True, data="插件已启用但无法读取温度")
            
            self.log_info(f"找到 {len(available_sensors)} 个温度传感器")
            
            # 开始监测
            self._start_monitoring()
            
            print(f"🌡️  CPU温度监测已启动，监测间隔: {self._monitor_interval}秒")
            return Response(success=True, data="CPU温度监测已启动")
            
        except Exception as e:
            self.log_error(f"启用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_disable(self) -> Response:
        """禁用插件时停止监测"""
        try:
            self._stop_monitoring()
            print("🛑 CPU温度监测已停止")
            return Response(success=True, data="CPU温度监测已停止")
            
        except Exception as e:
            self.log_error(f"禁用插件失败: {e}")
            return Response(success=False, data=str(e))
    
    def _handle_config_update(self, old_config: dict, new_config: dict) -> Response:
        """配置更新时重新启动监测"""
        try:
            old_interval = old_config.get('monitor_interval', 5.0)
            new_interval = new_config.get('monitor_interval', 5.0)
            
            if old_interval != new_interval:
                self._monitor_interval = new_interval
                
                # 如果正在监测，重新启动
                if self._monitoring:
                    self._stop_monitoring()
                    self._start_monitoring()
                    print(f"🔄 监测间隔已更新为: {new_interval}秒")
            
            return Response(success=True, data="配置更新成功")
            
        except Exception as e:
            self.log_error(f"配置更新失败: {e}")
            return Response(success=False, data=str(e))
    
    def _find_available_sensors(self) -> list:
        """查找可用的温度传感器"""
        available = []
        
        for thermal_path in self._thermal_paths:
            try:
                if Path(thermal_path).exists():
                    # 尝试读取一次确认可用
                    temp = self._read_temperature(thermal_path)
                    if temp is not None:
                        available.append(thermal_path)
            except Exception:
                continue
        
        return available
    
    def _read_temperature(self, thermal_path: str) -> float:
        """读取指定路径的温度"""
        try:
            with open(thermal_path, 'r') as f:
                temp_str = f.read().strip()
                # 温度值通常以毫摄氏度为单位，需要除以1000
                temp_millidegree = int(temp_str)
                temp_celsius = temp_millidegree / 1000.0
                return temp_celsius
        except Exception:
            return None
    
    def _get_all_temperatures(self) -> dict:
        """获取所有可用传感器的温度"""
        temperatures = {}
        
        for i, thermal_path in enumerate(self._thermal_paths):
            temp = self._read_temperature(thermal_path)
            if temp is not None:
                zone_name = f"thermal_zone{i}"
                temperatures[zone_name] = temp
        
        return temperatures
    
    def _start_monitoring(self):
        """开始监测"""
        if self._monitoring:
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name=f"CpuTempMonitor-{self.plugin_id}",
            daemon=True
        )
        self._monitor_thread.start()
        self.log_info("温度监测线程已启动")
    
    def _stop_monitoring(self):
        """停止监测"""
        if not self._monitoring:
            return
        
        self._monitoring = False
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        
        self.log_info("温度监测线程已停止")
    
    def _monitor_loop(self):
        """监测循环"""
        while self._monitoring:
            try:
                # 获取当前时间
                current_time = time.strftime("%H:%M:%S")
                
                # 获取所有温度
                temperatures = self._get_all_temperatures()
                
                if temperatures:
                    # 格式化输出
                    temp_info = []
                    max_temp = 0
                    
                    for zone, temp in temperatures.items():
                        temp_info.append(f"{zone}: {temp:.1f}°C")
                        max_temp = max(max_temp, temp)
                    
                    # 根据温度选择合适的emoji
                    if max_temp > 80:
                        emoji = "🔥"  # 高温
                    elif max_temp > 60:
                        emoji = "🌡️"  # 正常偏高
                    else:
                        emoji = "❄️"  # 正常
                    
                    # 打印温度信息
                    print(f"{emoji} [{current_time}] CPU温度: {' | '.join(temp_info)}")
                    
                    # 高温警告
                    if max_temp > 85:
                        print(f"⚠️  警告: CPU温度过高 ({max_temp:.1f}°C)!")
                
                else:
                    print(f"❌ [{current_time}] 无法读取CPU温度")
                
                # 等待下次监测
                time.sleep(self._monitor_interval)
                
            except Exception as e:
                self.log_error(f"温度监测异常: {e}")
                print(f"❌ 温度监测异常: {e}")
                time.sleep(self._monitor_interval)
    
    def _wait_for_graceful_shutdown(self):
        """等待优雅关闭"""
        self._stop_monitoring()
        time.sleep(0.1)
    
    # ==================== 插件接口 ====================
    
    def get_current_temperature(self, input_data: any = None) -> dict:
        """获取当前温度（插件接口）"""
        temperatures = self._get_all_temperatures()
        return {
            'timestamp': time.time(),
            'temperatures': temperatures,
            'max_temperature': max(temperatures.values()) if temperatures else None,
            'unit': 'Celsius'
        }
    
    def set_monitor_interval(self, input_data: dict) -> dict:
        """设置监测间隔（插件接口）"""
        try:
            interval = input_data.get('interval', 5.0)
            if interval < 1.0:
                return {'success': False, 'error': '监测间隔不能小于1秒'}
            
            self._monitor_interval = interval
            
            # 更新配置
            new_config = self.config.copy()
            new_config['monitor_interval'] = interval
            self.config = new_config
            
            # 如果正在监测，重新启动
            if self._monitoring:
                self._stop_monitoring()
                self._start_monitoring()
            
            return {'success': True, 'interval': interval}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}