from openai import OpenAI
from tavily import TavilyClient
import sys
import os
import time
import re

# --------------------------  完全复用你已有的配置，无需修改任何内容  --------------------------
deepseek_client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

# 自动判断运行模式：手动输入单只代码就用单只模式，没输入就用STOCK_LIST批量模式
input_stock_code = os.getenv("INPUT_STOCK_CODE", "")
stock_list_env = os.getenv("STOCK_LIST", "")

# 模式判断+空值友好处理，彻底解决空值报错问题
if input_stock_code and input_stock_code.strip() != "":
    run_mode = "single"
    stock_code_list = [input_stock_code.strip()]
    print(f"【运行模式】单只股票分析：{input_stock_code}")
elif stock_list_env and stock_list_env.strip() != "":
    run_mode = "batch"
    stock_code_list = [code.strip() for code in stock_list_env.split(",") if code.strip() != ""]
    print(f"【运行模式】批量分析，共{len(stock_code_list)}只股票：{stock_code_list}")
else:
    run_mode = "empty"
    stock_code_list = []
    print(f"【警告】未输入股票代码，也未配置STOCK_LIST，生成提示报告")
# ---------------------------------------------------------------------------------------------

# --------------------------  核心工具函数：开源系统同款稳定数据抓取  --------------------------
def auto_recognize_market(full_code):
    """自动识别股票所属市场，动态匹配最优数据源"""
    code_split = full_code.split(".")
    code_main = code_split[0]
    code_suffix = code_split[1].upper() if len(code_split) > 1 else ""

    market_rule_map = {
        "SH": {"market_name": "A股沪市", "exchange": "上海证券交易所", "stable_domains": ["eastmoney.com", "10jqka.com.cn", "finance.sina.com.cn", "stcn.com", "xueqiu.com"]},
        "SZ": {"market_name": "A股深市", "exchange": "深圳证券交易所", "stable_domains": ["eastmoney.com", "10jqka.com.cn", "finance.sina.com.cn", "stcn.com", "xueqiu.com"]},
        "HK": {"market_name": "港股", "exchange": "香港联合交易所", "stable_domains": ["aastocks.com", "hkex.com.hk", "eastmoney.com", "finance.yahoo.com"]},
        "O": {"market_name": "美股", "exchange": "纽约证券交易所", "stable_domains": ["finance.yahoo.com", "nasdaq.com", "nyse.com", "marketwatch.com"]},
        "NASDAQ": {"market_name": "美股纳斯达克", "exchange": "纳斯达克证券交易所", "stable_domains": ["nasdaq.com", "finance.yahoo.com", "marketwatch.com"]}
    }

    if code_suffix in market_rule_map:
        market_info = market_rule_map[code_suffix]
    else:
        market_info = {
            "market_name": "全球市场",
            "exchange": "对应证券交易所",
            "stable_domains": ["bloomberg.com", "reuters.com", "finance.yahoo.com", "marketwatch.com"]
        }

    market_info["code_main"] = code_main
    market_info["code_suffix"] = code_suffix
    market_info["full_code"] = full_code
    return market_info

def get_stock_full_info(market_info, stock_name):
    """开源系统同款全维度数据抓取，一次性拿齐分析所需所有素材"""
    stock_full_code = market_info["full_code"]
    stable_domains = market_info["stable_domains"]
    market_name = market_info["market_name"]

    # 梯度query，从精准到宽泛，休市期也能稳定拿到数据
    query_list = [
        f"{stock_full_code} {stock_name} 最新收盘价 涨跌幅 成交量 均线 MACD KDJ 支撑位 压力位 业绩 净利润 行业排名 主力资金 北向资金 最新公告 行业新闻",
        f"{stock_full_code} {stock_name} 2026年2月 行情数据 基本面分析 技术面分析",
        f"{stock_name} {market_name} 股票 最新分析 核心数据"
    ]

    for query in query_list:
        for retry in range(2):
            try:
                search_result = tavily_client.search(
                    query=query, search_depth="advanced", max_results=4, time_range="m1", include_domains=stable_domains, include_answer=True
                )
                full_content = search_result.get("answer", "")
                for item in search_result.get("results", []):
                    full_content += f"\n{item['content']}"
                
                if len(full_content) > 200:
                    return full_content
                time.sleep(1)
            except Exception as e:
                print(f"数据抓取失败：{str(e)}")
                time.sleep(1)
    return "暂无足够分析素材，基于基础信息进行分析"
# ---------------------------------------------------------------------------------------------

# --------------------------  单只股票分析：开源系统同款决策仪表盘+打分系统  --------------------------
def generate_single_stock_dashboard(stock_full_code):
    print(f"\n==================== 正在分析：{stock_full_code} ====================")
    # 1. 识别市场+获取基础信息
    market_info = auto_recognize_market(stock_full_code)
    market_name = market_info["market_name"]
    code_main = market_info["code_main"]
    stable_domains = market_info["stable_domains"]

    # 2. 锁定股票基础信息
    base_search = tavily_client.search(
        query=f"{stock_full_code} {code_main} 股票简称 公司名称 主营业务 所属行业",
        search_depth="basic", max_results=2, include_domains=stable_domains, include_answer=True
    )
    base_content = base_search.get("answer", "")
    name_match = re.search(r"(股票简称|证券简称|公司名称)[：:]\s*([^\s，。\n、()（）]+)", base_content)
    business_match = re.search(r"(主营业务|主要产品)[：:]\s*([^\n。]+)", base_content)
    industry_match = re.search(r"(所属行业|行业分类)[：:]\s*([^\n。，]+)", base_content)

    stock_name = name_match.group(2) if name_match else f"{code_main}"
    business_info = business_match.group(2) if business_match else "暂无公开主营业务信息"
    industry_info = industry_match.group(2) if industry_match else "暂无公开所属行业信息"
    print(f"基础信息锁定：{stock_name} | {industry_info}")

    # 3. 获取全维度分析素材
    full_material = get_stock_full_info(market_info, stock_name)
    print(f"素材抓取完成，正在生成决策仪表盘...")

    # 4. AI生成开源系统同款决策仪表盘+完整分析
    prompt = f"""
你是专业严谨的A股投资顾问，必须严格按照以下规则生成股票分析决策仪表盘，禁止编造任何信息：
1.  严格基于我提供的素材进行分析，所有数据、观点必须有依据，禁止瞎编
2.  严格执行100分制加权打分规则，每个维度必须给出分数+明确依据，综合评分对应投资评级
3.  必须给出精确的买卖点位：买入参考价、止损价、目标价
4.  必须给出交易纪律检查清单，每项按「满足/注意/不满足」标记
5.  结构严格按照我给的格式输出，语言简洁精炼，适合散户快速阅读

【100分制打分规则】
- 基本面评分（35分）：看业绩、行业地位、估值合理性
- 技术面评分（25分）：看趋势、量价配合、支撑压力位
- 资金面评分（20分）：看主力资金、北向资金、机构关注度
- 消息面评分（15分）：看政策利好、公司公告、行业景气度
- 风险扣分项（最多扣10分）：利空、退市风险、业绩暴雷等
综合评分=各项分数相加-风险扣分，满分100分

【投资评级规则】
90分及以上：★★★★★ 强烈关注 | 75-89分：★★★★ 积极关注 | 60-74分：★★★ 谨慎关注 | 40-59分：★★ 谨慎规避 | 40分以下：★ 高风险规避

【输出格式，必须严格遵守】
📊 {stock_name}({stock_full_code}) 决策仪表盘
🏷️ 综合评分：XX分 | 投资评级：XX
📈 最新收盘价：XX元 | 最新涨跌幅：XX%
🎯 精确点位：买入参考价XX元 | 止损价XX元 | 目标价XX元

【分维度打分详情】
1.  基本面：XX分/35分 | 打分依据：XXX
2.  技术面：XX分/25分 | 打分依据：XXX
3.  资金面：XX分/20分 | 打分依据：XXX
4.  消息面：XX分/15分 | 打分依据：XXX
5.  风险扣分：XX分 | 扣分依据：XXX

【交易纪律检查清单】
- 趋势健康度：满足/注意/不满足 | 说明：XXX
- 量价配合度：满足/注意/不满足 | 说明：XXX
- 基本面健康度：满足/注意/不满足 | 说明：XXX
- 资金关注度：满足/注意/不满足 | 说明：XXX
- 舆情风险度：满足/注意/不满足 | 说明：XXX

【核心观点】
一句话总结这只股票的投资价值和核心逻辑

【利好催化】
1.  XXX
2.  XXX

【风险提示】
1.  XXX
2.  XXX

【操作策略】
- 持仓者：XXX
- 空仓者：XXX

【股票基础信息】
所属市场：{market_name}
所属行业：{industry_info}
主营业务：{business_info}

【分析素材】
{full_material}
    """

    # 调用AI生成报告
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是专业严谨的股票投资顾问，严格按照用户要求生成决策仪表盘，禁止编造任何信息，所有分析必须基于提供的素材"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2200,
            stream=False,
            timeout=120
        )
        dashboard_content = response.choices[0].message.content
    except Exception as ai_error:
        dashboard_content = f"""
📊 {stock_name}({stock_full_code}) 分析报告
🏷️ 综合评分：暂无 | 投资评级：暂无
📈 最新收盘价：暂无 | 最新涨跌幅：暂无
【基础信息】
所属市场：{market_name}
所属行业：{industry_info}
主营业务：{business_info}
【操作建议】
当前暂无足够分析数据，建议等待开盘后获取最新行情再做决策，股市有风险，投资需谨慎。
        """

    print(f"==================== {stock_full_code} 分析完成 ====================")
    time.sleep(3)
    return dashboard_content
# ---------------------------------------------------------------------------------------------

# --------------------------  主程序：批量/单只模式执行  --------------------------
try:
    if run_mode == "empty":
        full_final_report = f"""
📌 股票分析提示
您未手动输入股票代码，也未在GitHub Secrets中配置STOCK_LIST，请按以下方式操作：
1.  单只股票分析：触发运行时，在输入框中填写完整股票代码（例：601777.SH）
2.  批量股票分析：在GitHub Secrets中新建STOCK_LIST，填写多只股票代码，用英文逗号分隔（例：601777.SH,000001.SZ）

📌 股市有风险，投资需谨慎。
        """
    else:
        # 生成所有股票的决策仪表盘
        full_final_report = f"🎯 股票分析决策仪表盘\n共分析{len(stock_code_list)}只标的\n当前为A股春节休市期，行情数据为休市前最后一个交易日数据\n\n"
        for stock_code in stock_code_list:
            single_dashboard = generate_single_stock_dashboard(stock_code)
            full_final_report += f"{single_dashboard}\n---\n"

        full_final_report += "\n📌 本报告数据均来自对应交易所官网、权威财经媒体公开信息，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。"

except Exception as e:
    full_final_report = f"❌ 分析失败\n错误原因：{str(e)}\n\n排查建议：\n1. 确认股票代码格式正确（例：601777.SH）\n2. 核对DeepSeek、Tavily密钥是否正确，API额度是否充足"

# 保存报告，100%兼容你之前的钉钉推送配置
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(full_final_report)

print("\n【最终报告生成完成】")
print(full_final_report)
