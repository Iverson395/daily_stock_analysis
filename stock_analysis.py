import akshare as ak
import sys

stock_code = sys.argv[1]

try:
    df = ak.stock_zh_a_spot_em()
    code = stock_code.split(".")[0]
    info = df[df["代码"] == code].iloc[0]

    res = f"""📊 实时股票分析
股票名称：{info['名称']}
代码：{stock_code}
现价：{info['最新价']} 元
涨跌幅：{info['涨跌幅']}%
开盘：{info['开盘']} 元
最高：{info['最高']} 元
最低：{info['最低']} 元
成交量：{round(info['成交量']/10000,2)} 万手
成交额：{round(info['成交额']/100000000,2)} 亿元
"""

except Exception as e:
    res = f"❌ 获取失败：{str(e)}"

with open("result.txt", "w", encoding="utf-8") as f:
    f.write(res)

print(res)
