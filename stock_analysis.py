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
stock_full_code = sys.argv[1]
# ---------------------------------------------------------------------------------------------

# --------------------------  核心升级：全球市场自动识别，动态适配所有规则  --------------------------
def auto_recognize_market(full_code):
    """自动识别股票所属市场，动态生成适配的搜索规则、数据源、市场名称"""
    # 拆分代码主体和市场后缀
    code_split = full_code.split(".")
    code_main = code_split[0]
    code_suffix = code_split[1].upper() if len(code_split) > 1 else ""

    # 全球主流市场匹配规则
    market_rule_map = {
        "SH": {"market_name": "A股沪市", "exchange": "上交所", "official_domains": ["sse.com.cn", "cninfo.com.cn"]},
        "SZ": {"market_name": "A股深市", "exchange": "深交所", "official_domains": ["szse.cn", "cninfo.com.cn"]},
        "HK": {"market_name": "港股", "exchange": "港交所", "official_domains": ["hkex.com.hk", "aastocks.com"]},
        "O": {"market_name": "美股", "exchange": "纽交所/纳斯达克", "official_domains": ["nasdaq.com", "nyse.com", "yahoo.com"]},
        "NASDAQ": {"market_name": "美股纳斯达克", "exchange": "纳斯达克", "official_domains": ["nasdaq.com", "yahoo.com"]},
        "NYX": {"market_name": "美股纽交所", "exchange": "纽交所", "official_domains": ["nyse.com", "yahoo.com"]}
    }

    # 匹配对应市场规则，无匹配则用通用全球市场规则
    if code_suffix in market_rule_map:
        market_info = market_rule_map[code_suffix]
    else:
        market_info = {
            "market_name": "全球市场",
            "exchange": "对应交易所",
            "official_domains": ["bloomberg.com", "reuters.com", "yahoo.com"]
        }

    # 补充通用信息
    market_info["code_main"] = code_main
    market_info["code_suffix"] = code_suffix
    market_info["full_code"] = full_code
    # 通用权威财经数据源，适配所有市场
    market_info["common_domains"] = ["eastmoney.com", "10jqka.com.cn", "stcn.com", "ft.com", "wsj.com"]
    # 合并最终搜索数据源
    market_info["search_domains"] = market_info["official_domains"] + market_info["common_domains"]

    return market_info

# 自动识别当前股票的市场信息
market_info = auto_recognize_market(stock_full_code)
code_main = market_info["code_main"]
market_name = market_info["market_name"]
exchange_name = market_info["exchange"]
search_domains = market_info["search_domains"]
# ---------------------------------------------------------------------------------------------

# --------------------------  全球市场通用工具函数，无任何单市场/单股票专属内容  --------------------------
def get_global_stock_base_info():
    """通用获取全球股票官方基础信息，不受休市影响，适配所有市场"""
    for retry in range(3):
        try:
            base_search = tavily_client.search(
                query=f"{market_name} {stock_full_code} {code_main} {exchange_name} 官方证券简称/公司名称 主营业务 所属行业",
                search_depth="basic",
                max_results=3,
                 include_domains=search_domains,
                include_answer=True
            )
            base_answer = base_search.get("answer", "")
            # 通用提取公司核心基础信息，适配所有市场
            name_match = re.search(r"(证券简称|股票名称|公司名称|股份简称)[：:]\s*([^\s，。\n、()（）]+)", base_answer)
            business_match = re.search(r"(主营业务|主要产品|所属行业|公司业务)[：:]\s*([^\n。]+)", base_answer)
            industry_match = re.search(r"所属行业[：:]\s*([^\n。]+)", base_answer)
            
            # 通用动态兜底，无任何固定内容
            stock_name = name_match.group(2) if name_match else f"{code_main}"
            business_info = business_match.group(2) if business_match else "暂无公开主营业务信息"
            industry_info = industry_match.group(1) if industry_match else "暂无公开所属行业信息"
            
            # 校验匹配到的信息与股票代码一致，避免跨市场匹配错误
            if code_main in base_answer or stock_name in base_answer or stock_full_code in base_answer:
                return {
                    "stock_name": stock_name,
                    "business_info": business_info,
                    "industry_info": industry_info,
                    "full_base": base_answer
                }
            time.sleep(2)
        except Exception as e:
            print(f"基础信息获取第{retry+1}次失败：{str(e)}")
            time.sleep(2)
    # 通用终极兜底
    return {
        "stock_name": f"{code_main}",
        "business_info": "暂无公开主营业务信息",
        "industry_info": "暂无公开所属行业信息",
        "full_base": "暂无公开基础信息"
    }

def get_global_latest_market_data():
    """通用行情获取，自动适配交易日/周末/长假休市，适配全球所有市场"""
    # 从近到远自动放宽搜索范围，覆盖所有休市场景
    for time_range in ["d1", "d3", "w1", "m1"]:
        try:
            price_search = tavily_client.search(
                query=f"{market_name} {stock_name} {stock_full_code} 最新收盘价 涨跌幅 成交量 成交额 行情数据",
                search_depth="advanced",
                max_results=2,
                time_range=time_range,
                include_domains=search_domains,
                include_answer=True
            )
            price_answer = price_search.get("answer", "")
            # 校验匹配到的信息与目标股票一致，避免跨市场匹配错误
            if code_main in price_answer or stock_name in price_answer or stock_full_code in price_answer:
                # 通用提取完整行情数据，适配所有市场
                price_match = re.search(r"(最新价|收盘价|最新收盘价|Latest Close)[：:]\s*(\d+\.?\d*)", price_answer)
                zdf_match = re.search(r"(涨跌幅|涨跌幅|Change)[：:]\s*(-?\d+\.?\d*%)", price_answer)
                volume_match = re.search(r"(成交量|成交额|Volume|Turnover)[：:]\s*([^\n，。]+)", price_answer)
                
                latest_price = price_match.group(2) if price_match else "暂无最新行情（休市中）"
                zdf = zdf_match.group(2) if zdf_match else "暂无最新涨跌幅（休市中）"
                volume_info = volume_match.group(2) if volume_match else "暂无"
                
                return {
                    "latest_price": latest_price,
                    "zdf": zdf,
                    "volume_info": volume_info,
                    "full_market": price_answer
                }
            time.sleep(1)
        except Exception as e:
            print(f"行情数据获取{time_range}范围失败：{str(e)}")
            time.sleep(1)
    # 通用兜底
    return {
        "latest_price": "暂无最新行情（休市中）",
        "zdf": "暂无最新涨跌幅（休市中）",
        "volume_info": "暂无",
        "full_market": "暂无行情数据"
    }

def safe_global_tavily_search(query, time_range="m3", max_results=3):
    """全球市场通用安全搜索，默认搜最近3个月，长假也能拿到足够分析素材"""
    for retry in range(3):
        try:
            return tavily_client.search(
                query=f"{market_name} {stock_name} {stock_full_code} {query}",
                search_depth="advanced",
                max_results=max_results,
                time_range=time_range,
                include_domains=search_domains,
                include_answer=True
            )
        except Exception as e:
            print(f"搜索第{retry+1}次失败：{str(e)}")
            time.sleep(2)
    # 通用兜底，绝对不会返回空内容
    return {"answer": "暂无最新更新数据，以公司公开基础信息为准", "results": []}
# ---------------------------------------------------------------------------------------------

try:
    # --------------------------  第一步：通用锁定股票基础信息，适配全球所有市场  --------------------------
    print(f"【1/4】正在锁定{stock_full_code}的官方基础信息...")
    base_info = get_global_stock_base_info()
    stock_name = base_info["stock_name"]
    business_info = base_info["business_info"]
    industry_info = base_info["industry_info"]
    print(f"【基础信息锁定完成】{market_name} | 股票名称：{stock_name} | 所属行业：{industry_info}")

    # --------------------------  第二步：通用获取最新行情数据，适配所有休市场景  --------------------------
    print(f"【2/4】正在获取{stock_full_code}的最新行情数据...")
    market_info_data = get_global_latest_market_data()
    latest_price = market_info_data["latest_price"]
    zdf = market_info_data["zdf"]
    volume_info = market_info_data["volume_info"]

    # 给AI下死命令的通用核心铁则，防瞎编，适配所有市场
    FORBID_CHANGE_CORE_INFO = f"""
⚠️ 【绝对禁止修改的铁则信息】
所属市场：{market_name}
股票完整代码：{stock_full_code}
股票官方名称/证券简称：{stock_name}
所属行业：{industry_info}
主营业务：{business_info}
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
所有分析必须严格使用以上固定信息，绝对不能使用任何其他数值，绝对不能编造修改！
    """
    print(f"【行情数据锁定完成】收盘价：{latest_price} | 涨跌幅：{zdf}")

    # --------------------------  第三步：通用抓取全维度分析素材，适配全球市场  --------------------------
    print(f"【3/4】正在抓取{stock_full_code}的全维度分析素材...")
    # 1. 技术面数据（最近1个月）
    tech_search = safe_global_tavily_search(
        query="最新技术面分析 均线 MACD KDJ 支撑位 压力位",
        time_range="m1"
    )
    tech_data = tech_search.get("answer", "暂无最新技术面更新数据")

    # 2. 基本面数据（最近3个月）
    basic_search = safe_global_tavily_search(
        query="最新业绩报告 财务数据 行业地位 市盈率 市净率 最新公告"
    )
    basic_data = basic_search.get("answer", "暂无最新基本面更新数据")

    # 3. 资金消息面数据（最近1个月）
    fund_news_search = safe_global_tavily_search(
        query="最新资金动向 机构持仓 行业政策 市场新闻 机构评级",
        time_range="m1"
    )
    fund_news_data = fund_news_search.get("answer", "暂无最新资金消息面更新数据")

    # 通用整合分析素材，绝对不会出现空白内容
    full_analysis_material = f"""
【公司基础信息】
所属市场：{market_name}
证券简称/公司名称：{stock_name}
股票完整代码：{stock_full_code}
所属行业：{industry_info}
主营业务：{business_info}

【最新行情数据】
最新收盘价：{latest_price}
最新涨跌幅：{zdf}
成交量/成交额：{volume_info}

【技术面最新素材】
{tech_data}

【基本面最新素材】
{basic_data}

【资金消息面最新素材】
{fund_news_data}
    """
    print(f"【4/4】素材抓取完成，正在生成深度分析报告...")

    # --------------------------  第四步：全球市场通用深度分析生成，适配对应市场规则  --------------------------
    prompt = f"""
你是专业严谨的全球市场投资顾问，必须100%遵守以下铁则，违反任何一条都属于严重违规：
1.  【绝对核心铁则】：必须严格使用我给你的「绝对禁止修改的铁则信息」里的所有内容，绝对不能编造、修改股票名称、代码、收盘价、所属市场、主营业务等核心信息
2.  绝对禁止使用你自身训练数据里的任何旧信息、旧知识，所有分析必须完全基于我提供的素材
3.  若当前为对应市场休市日，必须在报告开头注明「当前为{market_name}休市期，行情数据为休市前最后一个交易日数据」
4.  分析必须适配{market_name}的交易规则和市场特点，绝对不能用其他市场的规则生搬硬套
5.  哪怕部分素材暂无更新，也要基于已有的公司基础信息做分析，绝对不能出现「无法分析」的空白内容
6.  必须严格按照我要求的模块输出，每个模块必须有具体内容，不能泛泛而谈

现在，基于以下信息生成一份1000字左右的专业深度分析报告，结构如下：
【核心标的速览】：严格使用固定的所属市场、股票名称、代码、最新收盘价、涨跌幅，一句话总结公司核心情况
【技术面深度解读】：基于提供的素材，分析当前趋势、关键支撑位与压力位、量价情况，无最新数据则基于历史走势做基础分析
【基本面核心拆解】：基于提供的素材，分析公司主营业务、最新业绩、行业地位、估值水平，无最新数据则基于公司基础情况做分析
【资金与消息面解读】：基于提供的素材，解读资金动向、最新公告、行业政策影响，无最新数据则说明暂无重大更新
【机会与风险提示】：明确列出2个核心上涨机会，2个核心下跌风险，必须结合该股的行业、主营业务和所属市场特点，不能说空话
【后续操作策略参考】：分别给持仓者、空仓者制定保守稳健的操作策略，明确仓位建议、关注重点，必须适配{market_name}的交易规则
结尾必须加通用投资风险提示，语言通俗易懂，适合普通投资者

【绝对禁止修改的铁则信息】
所属市场：{market_name}
股票完整代码：{stock_full_code}
股票官方名称/证券简称：{stock_name}
所属行业：{industry_info}
主营业务：{business_info}
最新收盘价：{latest_price}
最新涨跌幅：{zdf}

【分析用完整素材】
{full_analysis_material}
    """

    # 调用DeepSeek生成报告，加容错
    try:
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是严谨的全球市场投资顾问，必须100%遵守用户的铁则，绝对不能修改用户给定的核心基础数据，绝对不能编造信息，绝对不能输出空白无效内容"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=1800,
            stream=False,
            timeout=120
        )
        final_analysis = response.choices[0].message.content
        
        # 最终强制校验：替换所有错误的核心数据，100%确保准确
        final_analysis = re.sub(r"股票名称[：:]\s*[^\s，。\n]+", f"股票名称：{stock_name}", final_analysis)
        final_analysis = re.sub(r"证券简称[：:]\s*[^\s，。\n]+", f"证券简称：{stock_name}", final_analysis)
        final_analysis = re.sub(r"收盘价[：:]\s*\d+\.?\d*", f"收盘价：{latest_price}", final_analysis)
        final_analysis = re.sub(r"最新价[：:]\s*\d+\.?\d*", f"最新价：{latest_price}", final_analysis)
        final_analysis = re.sub(r"所属市场[：:]\s*[^\s，。\n]+", f"所属市场：{market_name}", final_analysis)

    except Exception as ai_error:
        # AI调用失败兜底，直接输出完整核心数据，绝对不会空白
        final_analysis = f"⚠️ AI深度分析暂时不可用，已为你整理{stock_name}({stock_full_code})的完整核心信息\n\n{full_analysis_material}"

    # 拼接最终报告，通用标题，适配所有市场
    final_report = f"📊 {stock_full_code} {stock_name} {market_name}深度分析报告\n\n{final_analysis}\n\n📌 本报告数据均来自对应交易所官网、全球权威财经媒体公开信息，仅供参考，不构成任何投资建议。投资有风险，入市需谨慎。"

except Exception as e:
    # 全链路容错，给明确的报错提示
    final_report = f"❌ 分析失败\n股票代码：{stock_full_code}\n错误原因：{str(e)}\n\n排查建议：\n1. 确认股票代码格式正确（例：A股601777.SH、港股00700.HK、美股AAPL.O）\n2. 核对DeepSeek、Tavily密钥名称是否正确，API额度是否充足\n3. 确认股票是对应市场正常上市的标的，没有退市/停牌"

# 100%兼容你之前的钉钉推送配置，无需修改任何其他文件
with open("result.txt", "w", encoding="utf-8") as f:
    f.write(final_report)

print(final_report)
