同传 SI / SI Interpretation
==========================

Windows: 解压整个 SI 文件夹，双击 SI.exe。请保留 _internal 文件夹。
Ubuntu:  解压后运行 SI/SI。支持 Ubuntu 22.04 及以上的桌面环境。
macOS:   打开 SI/SI.app。请选择与处理器匹配的 x64 或 arm64 安装包。

无需安装 Python。第一次打开后，在设置中填写百炼地域、业务空间 ID 和 API Key。
实时翻译依赖网络和阿里云模型权限，可能产生 API 费用。
会议接入需要两条独立虚拟音频连接；安装包不包含音频驱动。

Ubuntu 桌面需具备 Qt 所需的系统库及 PulseAudio/PipeWire 音频环境。
如果缺少依赖，请参考项目 README 中的 Ubuntu 安装命令。
Linux 保存密钥需要可用且已解锁的 Secret Service 密钥环，例如 GNOME Keyring；
如无法保存，取消勾选保存密钥即可仅在本次运行中使用。
macOS 首次使用输入设备时需要允许麦克风访问。
当前下载包未使用 Windows 开发者证书签名，也未进行 Apple 公证。

项目与使用说明：https://github.com/KT-network/SI
问题反馈：https://github.com/KT-network/SI/issues
许可证：随包提供的 LICENSE（BSD 3-Clause）
