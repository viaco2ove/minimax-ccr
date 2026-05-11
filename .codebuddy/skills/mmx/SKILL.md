# MMX MiniMax CLI 技能
[mmx.conf](../../mmx.conf)
当 `mmx_enable=true` 时可用，使用 MiniMax MMX CLI 实现多种 AI 能力。

## 前提条件

- 已安装 mmx CLI: `npm install -g @minimaxi/mmx`
- 已登录: `mmx auth`
- 配置文件中设置 `mmx_enable=true`

## 命令

### 文本聊天
```bash
mmx text chat --message "你的问题"
```

### 文生图
```bash
mmx image "图片描述" --n 数量 --aspect-ratio 比例
# 例如: mmx image "一只穿宇航服的猫" --n 3 --aspect-ratio 16:9
```

### 视频生成（后台异步）
```bash
mmx video generate --prompt "视频描述" --async
```

### 语音合成
```bash
mmx speech synthesize --text "要合成的文本" --out 输出文件.mp3
```

### 音乐生成（带歌词）
```bash
mmx music generate --prompt "风格描述" --lyrics "歌词内容"
```

### 图像理解
```bash
mmx vision 图片路径
# 例如: mmx vision photo.jpg
```

### 网络搜索
```bash
mmx search "搜索关键词"
```

### 查配额
```bash
mmx quota
```

### 更新 CLI
```bash
mmx update https://platform.minimaxi.com/docs/token-plan/minimax-cli
```

## 使用示例

当用户请求生成图像、理解和分析图片时，优先使用 mmx vision。
当用户请求语音合成时，使用 mmx speech synthesize。