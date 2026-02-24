import requests
import hmac
import hashlib
import base64
import time

# 替换成你自己的配置
DINGTALK_WEBHOOK_URL = https://oapi.dingtalk.com/robot/send?access_token=b51161e3e798de38552fdd526b721deb6ceb6eb35ab58ddb12c79503ac9f45fb
DINGTALK_SECRET = SECa3b1879be2317eb00e311087a6877aa9a797499e61f34f849755f0b160834ddc

# 钉钉官方标准加签算法
def dingtalk_sign(secret: str):
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode("utf-8")
    string_to_sign = f"{timestamp}\n{secret}"
    string_to_sign_enc = string_to_sign.encode("utf-8")
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return {"timestamp": timestamp, "sign": sign}

# 测试推送
if __name__ == "__main__":
    print("开始钉钉推送测试...")
    # 强制添加关键词兜底，避免拦截
    title = "股票分析推送测试"
    content = f"# {title}\n\n✅ 测试成功！股票分析系统推送通道正常\n\n生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
    
    # 加签
    sign_data = dingtalk_sign(DINGTALK_SECRET)
    # 钉钉标准请求格式
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": content},
        "timestamp": sign_data["timestamp"],
        "sign": sign_data["sign"]
    }
    
    # 发送请求
    try:
        resp = requests.post(
            DINGTALK_WEBHOOK_URL,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=15
        )
        resp_json = resp.json()
        print(f"钉钉响应状态码：{resp.status_code}")
        print(f"钉钉返回内容：{resp_json}")
        if resp.status_code == 200 and resp_json.get("errcode") == 0:
            print("✅ 钉钉推送测试成功！请查看钉钉群消息")
        else:
            print(f"❌ 推送失败，错误原因：{resp_json.get('errmsg', '未知错误')}")
    except Exception as e:
        print(f"❌ 推送请求异常：{str(e)}")
