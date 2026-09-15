import os
import requests
from dotenv import load_dotenv

# 1. 自动读取您 .env 里的 Key
load_dotenv()
api_key = os.getenv("LLM_API_KEY")

if not api_key:
    print("❌ 错误：未找到 API Key，请检查 .env 文件是否配置正确。")
    exit()

print(f"🔑 正在测试 Key: {api_key[:5]}... (已隐藏后半部分)")

# 2. 向谷歌服务器询问：“这个 Key 能用哪些模型？”
url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
try:
    response = requests.get(url)
    data = response.json()
    
    if "error" in data:
        print(f"❌ API 返回错误: {data['error']['message']}")
    else:
        print("\n✅ 您的 Key 可以使用以下模型 (Name 就是您要填进 .env 的内容):")
        print("-" * 50)
        print(f"{'模型名称 (Name)':<40} | {'显示名称'}")
        print("-" * 50)
        
        valid_models = []
        for model in data.get('models', []):
            # 过滤出生成文本的模型
            if 'generateContent' in model['supportedGenerationMethods']:
                name = model['name']
                # 去掉 'models/' 前缀方便阅读，虽然API标准名是带models/的
                print(f"{name:<40} | {model['displayName']}")
                valid_models.append(name)
        
        print("-" * 50)
        print("\n💡 建议：")
        print("如果是用于 .env 配置，通常推荐使用: gemini-1.5-flash 或 gemini-1.5-pro")
        print("如果代码报错 404，请尝试填入完整路径: models/gemini-1.5-flash")

except Exception as e:
    print(f"❌ 网络请求失败: {e}")