#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI科技资讯抓取与推送脚本（腾讯翻译版）
每日抓取过去24小时的AI相关新闻，翻译后通过Server酱推送到微信，同时支持推送到飞书
"""

import os
import re
import json
import time
import hmac
import hashlib
import base64
import requests
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from typing import List, Dict

# 腾讯云SDK
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.tmt.v20180321 import tmt_client, models


class TencentTranslator:
    """腾讯云机器翻译"""
    
    def __init__(self, secret_id: str, secret_key: str, region: str = "ap-beijing"):
        """
        初始化腾讯翻译客户端
        
        Args:
            secret_id: 腾讯云 SecretId
            secret_key: 腾讯云 SecretKey
            region: 地域，可选 ap-beijing, ap-shanghai, ap-guangzhou 等
        """
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.cache = {}  # 翻译缓存
        self._init_client()
    
    def _init_client(self):
        """初始化腾讯云TMT客户端"""
        try:
            # 创建认证对象
            cred = credential.Credential(self.secret_id, self.secret_key)
            
            # 配置HTTP选项
            http_profile = HttpProfile()
            http_profile.endpoint = "tmt.tencentcloudapi.com"
            http_profile.reqTimeout = 30
            
            # 配置客户端选项
            client_profile = ClientProfile()
            client_profile.httpProfile = http_profile
            
            # 创建TMT客户端
            self.client = tmt_client.TmtClient(cred, self.region, client_profile)
            print("✓ 腾讯翻译客户端初始化成功")
            
        except Exception as e:
            print(f"✗ 腾讯翻译客户端初始化失败: {str(e)}")
            self.client = None
    
    def is_chinese(self, text: str) -> bool:
        """检测文本是否主要是中文"""
        if not text:
            return True
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        return chinese_chars / len(text) > 0.3
    
    def translate(self, text: str, source: str = "auto", target: str = "zh") -> str:
        """
        翻译文本
        
        Args:
            text: 要翻译的文本
            source: 源语言，auto表示自动检测
            target: 目标语言，zh表示中文
            
        Returns:
            翻译后的文本，失败返回空字符串
        """
        if not text or not self.client:
            return ""
        
        # 如果已经是中文，不需要翻译
        if self.is_chinese(text):
            return ""
        
        # 检查缓存
        cache_key = text[:100]
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        try:
            # 创建请求对象
            req = models.TextTranslateRequest()
            
            # 限制文本长度（腾讯API单次限制2000字符）
            text_to_translate = text[:2000] if len(text) > 2000 else text
            
            # 设置请求参数
            req.SourceText = text_to_translate
            req.Source = source
            req.Target = target
            req.ProjectId = 0  # 项目ID，默认为0
            
            # 发送请求
            resp = self.client.TextTranslate(req)
            
            # 解析响应
            result = json.loads(resp.to_json_string())
            translated_text = result.get("TargetText", "")
            
            # 缓存结果
            if translated_text:
                self.cache[cache_key] = translated_text
            
            # 添加延迟，避免QPS超限（腾讯API限制5次/秒）
            time.sleep(0.3)
            
            return translated_text
            
        except TencentCloudSDKException as e:
            print(f"  腾讯翻译API错误: {e.message}")
            return ""
        except Exception as e:
            print(f"  翻译失败: {str(e)}")
            return ""


class GitHubTrendingFetcher:
    """GitHub热门大模型/Agent开源项目抓取器（最近24小时star增加最多）"""

    # LLM/Agent 相关关键词，用于过滤 Trending 列表
    LLM_AGENT_KEYWORDS = [
        "llm", "agent", "gpt", "language model", "large language",
        "chatbot", "openai", "langchain", "rag", "retrieval",
        "fine-tun", "finetune", "transformer", "diffusion",
        "multimodal", "embedding", "vector", "inference",
        "ollama", "vllm", "llamaindex", "autogen", "crewai",
        "anthropic", "gemini", "mistral", "llama", "qwen",
        "deepseek", "phi-", "claude", "copilot", "ai agent",
        "autonomous agent", "workflow", "mcp",
    ]

    # GitHub Trending 页面抓取目标（按日统计，涵盖主流 AI 开发语言）
    TRENDING_URLS = [
        "https://github.com/trending?since=daily",
        "https://github.com/trending/python?since=daily",
        "https://github.com/trending/javascript?since=daily",
        "https://github.com/trending/typescript?since=daily",
        "https://github.com/trending/rust?since=daily",
        "https://github.com/trending/go?since=daily",
    ]

    # 当 Trending 抓取结果不足时的兜底 GitHub Search 查询
    FALLBACK_QUERIES = [
        "topic:llm topic:agent",
        "topic:large-language-model",
        "topic:llm-agent",
        "topic:ai-agent",
    ]

    def __init__(self, github_token: str = None):
        """
        初始化 GitHub 热门项目抓取器

        Args:
            github_token: GitHub Personal Access Token（可选，提高 API 速率限制）
        """
        self.api_headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if github_token:
            self.api_headers["Authorization"] = f"Bearer {github_token}"

        self.web_headers = {
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _is_llm_agent_related(self, repo: Dict) -> bool:
        """判断一个项目是否与 LLM / Agent 相关"""
        text = " ".join([
            repo.get("name", "").lower(),
            repo.get("description", "").lower(),
            " ".join(repo.get("topics", [])).lower(),
        ])
        return any(kw in text for kw in self.LLM_AGENT_KEYWORDS)

    def _parse_star_count(self, text: str) -> int:
        """将 '1,234' 或 '1.2k' 等字符串转换为整数"""
        text = text.strip().replace(",", "").replace(" ", "")
        if text.endswith("k") or text.endswith("K"):
            return int(float(text[:-1]) * 1000)
        try:
            return int(text)
        except ValueError:
            return 0

    # ------------------------------------------------------------------
    # GitHub Trending 页面抓取
    # ------------------------------------------------------------------

    def _scrape_trending_page(self, url: str) -> List[Dict]:
        """
        抓取一个 GitHub Trending 页面，返回项目列表。

        每个项目包含：name, url, description, language, stars, forks,
        stars_today, topics, updated_at
        """
        repos: List[Dict] = []
        try:
            response = requests.get(url, headers=self.web_headers, timeout=30)
            if response.status_code != 200:
                print(f"  ⚠️ Trending 页面返回 {response.status_code}: {url}")
                return repos

            soup = BeautifulSoup(response.text, "html.parser")
            articles = soup.select("article.Box-row")

            for article in articles:
                try:
                    # 仓库名称 & URL
                    h2 = article.select_one("h2")
                    if not h2:
                        continue
                    a_tag = h2.select_one("a")
                    if not a_tag:
                        continue
                    repo_path = a_tag.get("href", "").strip("/")
                    if not repo_path or repo_path.count("/") != 1:
                        continue
                    repo_url = f"https://github.com/{repo_path}"
                    repo_name = repo_path  # "owner/repo"

                    # 描述
                    p_tag = article.select_one("p")
                    description = p_tag.get_text(strip=True) if p_tag else ""

                    # 编程语言
                    lang_span = article.select_one(
                        'span[itemprop="programmingLanguage"]'
                    )
                    language = lang_span.get_text(strip=True) if lang_span else ""

                    # 总 Stars & Forks（页面上的小图标链接）
                    stat_links = article.select("a.Link--muted")
                    stars = 0
                    forks = 0
                    for link in stat_links:
                        href = link.get("href", "")
                        text = link.get_text(strip=True)
                        if "/stargazers" in href:
                            stars = self._parse_star_count(text)
                        elif "/forks" in href:
                            forks = self._parse_star_count(text)

                    # 今日 star 增量
                    stars_today = 0
                    for span in article.select("span"):
                        span_text = span.get_text(strip=True)
                        if "stars today" in span_text or "star today" in span_text:
                            # 格式："1,234 stars today"
                            parts = span_text.split()
                            if parts:
                                stars_today = self._parse_star_count(parts[0])
                            break

                    repos.append(
                        {
                            "name": repo_name,
                            "description": description,
                            "stars": stars,
                            "forks": forks,
                            "stars_today": stars_today,
                            "url": repo_url,
                            "language": language,
                            "topics": [],          # topics 通过 API 补充
                            "updated_at": "",      # updated_at 通过 API 补充
                        }
                    )
                except Exception as exc:
                    print(f"  ⚠️ 解析 Trending 条目失败: {exc}")

        except Exception as exc:
            print(f"  ✗ 抓取 Trending 页面失败 ({url}): {exc}")

        return repos

    def _enrich_with_api(self, repo: Dict) -> Dict:
        """
        通过 GitHub API 补充项目的 topics 字段（及修正 stars/forks 数据）。
        失败时静默返回原始 repo。
        """
        try:
            api_url = f"https://api.github.com/repos/{repo['name']}"
            response = requests.get(api_url, headers=self.api_headers, timeout=15)
            if response.status_code == 200:
                data = response.json()
                repo["topics"] = data.get("topics", [])
                repo["stars"] = data.get("stargazers_count", repo["stars"])
                repo["forks"] = data.get("forks_count", repo["forks"])
                try:
                    repo["updated_at"] = datetime.fromisoformat(
                        data["updated_at"].replace("Z", "+00:00")
                    ).strftime("%Y-%m-%d")
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass
        return repo

    # ------------------------------------------------------------------
    # GitHub Search API 兜底
    # ------------------------------------------------------------------

    def _fetch_from_search_api(self, count: int) -> List[Dict]:
        """
        当 Trending 页面无法提供足够的 LLM/Agent 项目时，
        通过 GitHub Search API 查询最近 7 天内有推送活动的相关项目。
        """
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        all_repos: List[Dict] = []
        seen_ids: set = set()

        for query in self.FALLBACK_QUERIES:
            try:
                url = "https://api.github.com/search/repositories"
                params = {
                    "q": f"{query} pushed:>{since} stars:>100",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 30,
                }
                response = requests.get(
                    url, headers=self.api_headers, params=params, timeout=30
                )
                if response.status_code == 200:
                    data = response.json()
                    for repo in data.get("items", []):
                        if repo["id"] not in seen_ids:
                            seen_ids.add(repo["id"])
                            description = repo.get("description") or ""
                            try:
                                updated_date = datetime.fromisoformat(
                                    repo["updated_at"].replace("Z", "+00:00")
                                ).strftime("%Y-%m-%d")
                            except (ValueError, KeyError):
                                updated_date = repo.get("updated_at", "")[:10]
                            all_repos.append(
                                {
                                    "name": repo["full_name"],
                                    "description": description,
                                    "stars": repo["stargazers_count"],
                                    "forks": repo["forks_count"],
                                    "stars_today": 0,
                                    "url": repo["html_url"],
                                    "language": repo.get("language") or "",
                                    "topics": repo.get("topics", []),
                                    "updated_at": updated_date,
                                }
                            )
                elif response.status_code == 403:
                    print(
                        "  ⚠️ GitHub API 速率限制，请配置 GITHUB_TOKEN 环境变量以提高限额"
                    )
                    break
                else:
                    print(f"  ⚠️ GitHub API 返回异常状态码: {response.status_code}")

                time.sleep(1)

            except Exception as exc:
                print(f"  ✗ GitHub Search API 抓取失败: {exc}")

        all_repos.sort(key=lambda x: x["stars"], reverse=True)
        return all_repos[:count]

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def fetch_trending_repos(self, count: int = 10) -> List[Dict]:
        """
        获取最近 24 小时 star 增加最多的 LLM/Agent 开源项目。

        优先从 GitHub Trending（每日）页面抓取并过滤，结果不足时
        通过 GitHub Search API 兜底。

        Args:
            count: 返回项目数量，默认 10

        Returns:
            项目列表，按今日 star 增量降序排列
        """
        all_trending: List[Dict] = []
        seen_names: set = set()

        # 1. 从各 Trending 页面收集候选项目
        for url in self.TRENDING_URLS:
            repos = self._scrape_trending_page(url)
            for repo in repos:
                if repo["name"] not in seen_names:
                    seen_names.add(repo["name"])
                    all_trending.append(repo)
            time.sleep(0.5)

        print(f"  从 Trending 页面共收集到 {len(all_trending)} 个项目，开始过滤 LLM/Agent 相关项目…")

        # 2. 过滤 LLM/Agent 相关项目
        llm_repos = [r for r in all_trending if self._is_llm_agent_related(r)]
        print(f"  过滤后剩余 {len(llm_repos)} 个 LLM/Agent 相关项目")

        # 3. 若不足 count 个，通过 GitHub Search API 兜底
        if len(llm_repos) < count:
            print(f"  LLM/Agent Trending 项目不足 {count} 个，启用 GitHub Search API 兜底…")
            fallback = self._fetch_from_search_api(count - len(llm_repos))
            existing_repo_names = {r["name"] for r in llm_repos}
            for repo in fallback:
                if repo["name"] not in existing_repo_names:
                    existing_repo_names.add(repo["name"])
                    llm_repos.append(repo)

        # 4. 按今日 star 增量降序排列，取前 count 个
        llm_repos.sort(key=lambda x: x["stars_today"], reverse=True)
        top_repos = llm_repos[:count]

        # 5. 通过 API 补充 topics / updated_at 等详细信息
        #    来自 Trending 页面的项目 topics 为空列表，需要通过 API 补充；
        #    来自 Search API 兜底的项目已包含完整信息，无需重复请求。
        enriched: List[Dict] = []
        for repo in top_repos:
            if not repo.get("topics"):
                repo = self._enrich_with_api(repo)
                time.sleep(0.3)
            enriched.append(repo)

        print(f"✓ 共获取到 {len(enriched)} 个最近24小时 star 增加最多的 LLM/Agent 开源项目")
        return enriched


class AINewsFetcher:
    """AI新闻抓取器"""
    
    # 国际AI科技相关RSS源列表
    RSS_SOURCES = [
        {
            "name": "MIT Technology Review - AI",
            "url": "https://www.technologyreview.com/feed/",
            "keywords": ["AI", "artificial intelligence", "machine learning", "GPT", "LLM"]
        },
        {
            "name": "VentureBeat AI",
            "url": "https://venturebeat.com/category/ai/feed/",
            "keywords": None
        },
        {
            "name": "The Verge - AI",
            "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
            "keywords": None
        },
        {
            "name": "Ars Technica",
            "url": "https://feeds.arstechnica.com/arstechnica/index",
            "keywords": ["AI", "artificial intelligence", "ChatGPT", "OpenAI", "Google AI", "machine learning"]
        },
        {
            "name": "TechCrunch AI",
            "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
            "keywords": None
        },
        {
            "name": "Hacker News",
            "url": "https://hnrss.org/newest?q=AI+OR+LLM+OR+GPT+OR+artificial+intelligence",
            "keywords": None
        },
        {
            "name": "AI News",
            "url": "https://www.artificialintelligence-news.com/feed/",
            "keywords": None
        },
    ]

    # 中国国内AI科技相关RSS源列表
    CHINESE_RSS_SOURCES = [
        {
            "name": "机器之心",
            "url": "https://www.jiqizhixin.com/rss",
            "keywords": None
        },
        {
            "name": "量子位",
            "url": "https://www.qbitai.com/feed",
            "keywords": None
        },
        {
            "name": "新智元",
            "url": "https://feeds.feedburner.com/xinzhiyuan",
            "keywords": None
        },
        {
            "name": "36氪",
            "url": "https://36kr.com/feed",
            "keywords": ["AI", "人工智能", "大模型", "机器学习", "ChatGPT", "大语言模型"]
        },
        {
            "name": "爱范儿",
            "url": "https://www.ifanr.com/feed",
            "keywords": ["AI", "人工智能", "大模型", "机器学习"]
        },
        {
            "name": "虎嗅",
            "url": "https://www.huxiu.com/rss/0.xml",
            "keywords": ["AI", "人工智能", "大模型", "机器学习", "ChatGPT"]
        },
        {
            "name": "雷锋网",
            "url": "https://www.leiphone.com/feed",
            "keywords": ["AI", "人工智能", "大模型", "机器学习"]
        },
        {
            "name": "极客公园",
            "url": "https://www.geekpark.net/rss",
            "keywords": ["AI", "人工智能", "大模型", "机器学习"]
        },
        {
            "name": "InfoQ中文",
            "url": "https://www.infoq.cn/feed",
            "keywords": ["AI", "人工智能", "大模型", "机器学习", "LLM"]
        },
        {
            "name": "OSCHINA",
            "url": "https://www.oschina.net/news/rss",
            "keywords": ["AI", "人工智能", "大模型", "机器学习", "LLM", "深度学习"]
        },
    ]
    
    def __init__(self, translator: TencentTranslator = None):
        self.news_items: List[Dict] = []
        self.chinese_news_items: List[Dict] = []
        self.github_repos: List[Dict] = []
        self.time_threshold = datetime.now(timezone.utc) - timedelta(hours=24)
        self.translator = translator
    
    def fetch_from_rss(self, source: Dict) -> List[Dict]:
        """从单个RSS源抓取新闻"""
        items = []
        try:
            print(f"正在抓取: {source['name']}")
            feed = feedparser.parse(source['url'])
            
            for entry in feed.entries:
                # 解析发布时间
                pub_time = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_time = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    pub_time = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                
                # 检查是否在24小时内
                if pub_time and pub_time < self.time_threshold:
                    continue
                
                # 获取标题和链接
                title = entry.get('title', 'No Title')
                link = entry.get('link', '')
                summary = entry.get('summary', '')[:300] if entry.get('summary') else ''
                
                # 清理HTML标签
                summary = re.sub(r'<[^>]+>', '', summary)
                
                # 如果有关键词过滤
                if source['keywords']:
                    text_to_check = (title + ' ' + summary).lower()
                    if not any(kw.lower() in text_to_check for kw in source['keywords']):
                        continue
                
                items.append({
                    'title': title,
                    'link': link,
                    'source': source['name'],
                    'pub_time': pub_time.strftime('%Y-%m-%d %H:%M') if pub_time else 'Unknown',
                    'summary': summary
                })
            
            print(f"  → 获取到 {len(items)} 条相关新闻")
            
        except Exception as e:
            print(f"  ✗ 抓取失败: {str(e)}")
        
        return items
    
    def translate_news(self):
        """为所有新闻添加中文翻译"""
        if not self.translator:
            print("⚠️ 未配置翻译器，跳过翻译")
            return
        
        print("\n🌐 正在使用腾讯翻译API翻译新闻...")
        
        for i, item in enumerate(self.news_items):
            print(f"  翻译进度: {i+1}/{len(self.news_items)} - {item['title'][:30]}...")
            
            # 翻译标题
            item['title_cn'] = self.translator.translate(item['title'])
            
            # 翻译摘要（如果有）
            if item.get('summary'):
                item['summary_cn'] = self.translator.translate(item['summary'])
            else:
                item['summary_cn'] = ""
        
        print("✓ 翻译完成")
    
    def fetch_all(self) -> List[Dict]:
        """从所有RSS源抓取新闻（国际 + 国内）"""
        # ── 国际新闻 ──────────────────────────────────────────
        all_items = []
        for source in self.RSS_SOURCES:
            items = self.fetch_from_rss(source)
            all_items.extend(items)
            time.sleep(1)

        # 去重
        seen_titles = set()
        unique_items = []
        for item in all_items:
            title_key = item['title'][:30].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_items.append(item)

        # 按时间排序，限制数量以控制翻译时间和API调用次数
        unique_items.sort(key=lambda x: x['pub_time'], reverse=True)
        self.news_items = unique_items[:20]

        # 翻译国际新闻
        self.translate_news()

        # ── 国内新闻 ──────────────────────────────────────────
        print("\n📡 开始抓取国内AI科技资讯...\n")
        self.fetch_chinese_news()

        return self.news_items

    def fetch_chinese_news(self):
        """从国内RSS源抓取AI科技新闻（最多10条）"""
        all_cn_items = []

        for source in self.CHINESE_RSS_SOURCES:
            items = self.fetch_from_rss(source)
            all_cn_items.extend(items)
            time.sleep(1)

        # 去重
        seen_titles = set()
        unique_cn_items = []
        for item in all_cn_items:
            title_key = item['title'][:30].lower()
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                unique_cn_items.append(item)

        # 按时间排序，限制为10条
        unique_cn_items.sort(key=lambda x: x['pub_time'], reverse=True)
        self.chinese_news_items = unique_cn_items[:10]

        print(f"✓ 共获取国内AI资讯 {len(self.chinese_news_items)} 条")
    
    def format_for_wechat(self) -> tuple:
        """格式化新闻内容用于微信推送（中英双语版）"""
        if not self.news_items and not self.chinese_news_items and not self.github_repos:
            return "今日AI资讯", "暂无最新AI科技资讯"

        title = f"📰 AI科技日报 ({datetime.now().strftime('%Y-%m-%d')})"

        content_lines = [
            f"## 🤖 过去24小时AI科技要闻\n",
            f"共收集到 **{len(self.news_items)}** 条国际资讯（中英双语）、**{len(self.chinese_news_items)}** 条国内资讯"
            + (f"、**{len(self.github_repos)}** 个GitHub热门大模型/Agent项目" if self.github_repos else "")
            + "\n",
            "---\n"
        ]

        # ── 国际新闻 ─────────────────────────────────────────
        if self.news_items:
            content_lines.append("## 🌍 国际AI科技资讯\n\n")
            for i, item in enumerate(self.news_items, 1):
                content_lines.append(f"### {i}. {item['title']}\n")

                if item.get('title_cn'):
                    content_lines.append(f"**📝 中文：** {item['title_cn']}\n\n")

                if item.get('summary_cn'):
                    summary_display = item['summary_cn'][:150]
                    if len(item['summary_cn']) > 150:
                        summary_display += "..."
                    content_lines.append(f"> 💡 {summary_display}\n\n")

                content_lines.append(
                    f"- 🔗 来源: {item['source']}\n"
                    f"- 🕐 时间: {item['pub_time']}\n"
                    f"- 📎 [阅读原文]({item['link']})\n\n"
                )

        # ── 国内新闻 ─────────────────────────────────────────
        if self.chinese_news_items:
            content_lines.append("---\n\n")
            content_lines.append("## 🇨🇳 国内AI科技资讯\n\n")
            for i, item in enumerate(self.chinese_news_items, 1):
                content_lines.append(f"### {i}. {item['title']}\n")

                if item.get('summary'):
                    summary_display = item['summary'][:150]
                    if len(item['summary']) > 150:
                        summary_display += "..."
                    content_lines.append(f"> 💡 {summary_display}\n\n")

                content_lines.append(
                    f"- 🔗 来源: {item['source']}\n"
                    f"- 🕐 时间: {item['pub_time']}\n"
                    f"- 📎 [阅读原文]({item['link']})\n\n"
                )

        # ── GitHub热门大模型/Agent项目 ──────────────────────────
        if self.github_repos:
            content_lines.append("---\n\n")
            content_lines.append("## 🔥 GitHub LLM/Agent 开源项目·24小时 Star 飙升榜 TOP 10\n\n")
            for i, repo in enumerate(self.github_repos, 1):
                content_lines.append(f"### {i}. [{repo['name']}]({repo['url']})\n")

                if repo.get('description'):
                    content_lines.append(f"> {repo['description']}\n\n")

                lang_str = f"语言: {repo['language']}  " if repo.get('language') else ""
                topics_str = ""
                if repo.get('topics'):
                    topics_str = f"标签: {', '.join(repo['topics'][:5])}  "
                stars_today = repo.get('stars_today', 0)
                today_str = f"🚀 今日新增: +{stars_today:,}  " if stars_today else ""
                updated_str = f"- 🕐 最近更新: {repo['updated_at']}\n\n" if repo.get('updated_at') else "\n"
                content_lines.append(
                    f"- {today_str}⭐ Stars: {repo['stars']:,}  🍴 Forks: {repo['forks']:,}\n"
                    f"- {lang_str}{topics_str}\n"
                    + updated_str
                )

        content_lines.append("\n---\n")
        content_lines.append(f"*由 GitHub Actions 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
        content_lines.append("*翻译由腾讯云机器翻译提供*")

        content = ''.join(content_lines)
        return title, content

    def format_for_feishu(self) -> Dict:
        """格式化新闻内容为飞书富文本消息（post类型）"""
        date_str = datetime.now().strftime('%Y-%m-%d')
        msg_title = f"📰 AI科技日报 ({date_str})"

        paragraphs = []

        def _text(t: str) -> Dict:
            return {"tag": "text", "text": t}

        def _link(text: str, href: str) -> Dict:
            return {"tag": "a", "text": text, "href": href}

        def _bold(t: str) -> Dict:
            return {"tag": "text", "text": t}

        # 摘要行
        total_intl = len(self.news_items)
        total_cn = len(self.chinese_news_items)
        total_gh = len(self.github_repos)
        summary_text = f"🤖 过去24小时：{total_intl} 条国际资讯，{total_cn} 条国内资讯"
        if total_gh:
            summary_text += f"，{total_gh} 个GitHub热门大模型/Agent项目"
        paragraphs.append([_text(summary_text)])
        paragraphs.append([_text("─" * 30)])

        # ── 国际新闻 ─────────────────────────────────────────
        if self.news_items:
            paragraphs.append([_bold("🌍 国际AI科技资讯")])
            for i, item in enumerate(self.news_items, 1):
                # 标题行（带链接）
                title_text = item.get('title_cn') or item['title']
                row = [_text(f"{i}. ")]
                if item.get('link'):
                    row.append(_link(title_text, item['link']))
                else:
                    row.append(_text(title_text))
                paragraphs.append(row)

                # 英文原标题（若有中文翻译则附注）
                if item.get('title_cn'):
                    paragraphs.append([_text(f"   EN: {item['title']}")])

                # 摘要
                if item.get('summary_cn'):
                    summary = item['summary_cn'][:120]
                    if len(item['summary_cn']) > 120:
                        summary += "..."
                    paragraphs.append([_text(f"   💡 {summary}")])

                paragraphs.append([
                    _text(f"   🔗 {item['source']}  🕐 {item['pub_time']}")
                ])

        # ── 国内新闻 ─────────────────────────────────────────
        if self.chinese_news_items:
            paragraphs.append([_text("─" * 30)])
            paragraphs.append([_bold("🇨🇳 国内AI科技资讯")])
            for i, item in enumerate(self.chinese_news_items, 1):
                row = [_text(f"{i}. ")]
                if item.get('link'):
                    row.append(_link(item['title'], item['link']))
                else:
                    row.append(_text(item['title']))
                paragraphs.append(row)

                if item.get('summary'):
                    summary = item['summary'][:120]
                    if len(item['summary']) > 120:
                        summary += "..."
                    paragraphs.append([_text(f"   💡 {summary}")])

                paragraphs.append([
                    _text(f"   🔗 {item['source']}  🕐 {item['pub_time']}")
                ])

        # ── GitHub热门大模型/Agent项目 ──────────────────────────
        if self.github_repos:
            paragraphs.append([_text("─" * 30)])
            paragraphs.append([_bold("🔥 GitHub LLM/Agent 开源项目·24小时 Star 飙升榜 TOP 10")])
            for i, repo in enumerate(self.github_repos, 1):
                row = [_text(f"{i}. ")]
                row.append(_link(repo['name'], repo['url']))
                paragraphs.append(row)

                if repo.get('description'):
                    desc = repo['description'][:120]
                    if len(repo['description']) > 120:
                        desc += "..."
                    paragraphs.append([_text(f"   💡 {desc}")])

                lang_str = f"  语言: {repo['language']}" if repo.get('language') else ""
                stars_today = repo.get('stars_today', 0)
                today_str = f"  🚀 今日+{stars_today:,}" if stars_today else ""
                updated_str = f"  🕐 {repo['updated_at']}" if repo.get('updated_at') else ""
                paragraphs.append([
                    _text(f"   ⭐ {repo['stars']:,}  🍴 {repo['forks']:,}{today_str}{lang_str}{updated_str}")
                ])

        paragraphs.append([_text("─" * 30)])
        paragraphs.append([
            _text(f"由 GitHub Actions 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        ])

        return {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": msg_title,
                        "content": paragraphs
                    }
                }
            }
        }


class ServerChanPusher:
    """Server酱推送器"""
    
    def __init__(self, sendkey: str):
        self.sendkey = sendkey
        self.api_url = f"https://sctapi.ftqq.com/{sendkey}.send"
    
    def push(self, title: str, content: str) -> bool:
        """推送消息到微信"""
        try:
            if len(title) > 256:
                title = title[:253] + "..."
            
            data = {
                "title": title,
                "desp": content,
            }
            
            response = requests.post(self.api_url, data=data, timeout=30)
            result = response.json()
            
            if result.get('code') == 0:
                print(f"✓ 微信消息推送成功！")
                return True
            else:
                print(f"✗ 微信消息推送失败: {result.get('message', 'Unknown error')}")
                return False
                
        except Exception as e:
            print(f"✗ 微信推送异常: {str(e)}")
            return False


class FeishuPusher:
    """飞书自定义机器人推送器（Webhook方式）"""

    def __init__(self, webhook_url: str, secret: str = None):
        """
        初始化飞书推送器

        Args:
            webhook_url: 飞书自定义机器人的 Webhook 地址
                         格式: https://open.feishu.cn/open-apis/bot/v2/hook/<token>
            secret: 飞书机器人安全设置中的签名校验密钥（可选）。
                    若在飞书机器人设置中开启了「签名校验」，则必须填写对应的签名密钥，
                    否则每次推送均会失败。
        """
        self.webhook_url = webhook_url
        self.secret = secret

    def _generate_sign(self, timestamp: str) -> str:
        """
        生成飞书签名

        算法：HMAC-SHA256(timestamp + "\\n" + secret, "") -> base64

        Args:
            timestamp: Unix 时间戳（秒），字符串形式

        Returns:
            base64 编码后的签名字符串
        """
        sign_str = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(sign_str.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def push(self, payload: Dict) -> bool:
        """
        向飞书推送消息

        Args:
            payload: 符合飞书消息格式的字典（msg_type + content）

        Returns:
            推送成功返回 True，否则返回 False
        """
        try:
            # 若配置了签名密钥，将 timestamp 和 sign 注入 payload
            if self.secret:
                timestamp = str(int(time.time()))
                sign = self._generate_sign(timestamp)
                payload = dict(payload)  # 避免修改原始对象
                payload["timestamp"] = timestamp
                payload["sign"] = sign

            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            result = response.json()

            # 飞书返回 {"StatusCode": 0} 或 {"code": 0} 表示成功
            if result.get('StatusCode') == 0 or result.get('code') == 0:
                print("✓ 飞书消息推送成功！")
                return True
            else:
                msg = result.get('msg') or result.get('message') or str(result)
                print(f"✗ 飞书消息推送失败: {msg}")
                return False

        except Exception as e:
            print(f"✗ 飞书推送异常: {str(e)}")
            return False


def main():
    """主函数"""
    print("=" * 60)
    print("AI科技资讯抓取与推送（腾讯翻译版）")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 从环境变量获取配置
    sendkey = os.environ.get('SERVERCHAN_SENDKEY')
    tencent_secret_id = os.environ.get('TENCENT_SECRET_ID')
    tencent_secret_key = os.environ.get('TENCENT_SECRET_KEY')
    feishu_webhook_url = os.environ.get('FEISHU_WEBHOOK_URL')
    feishu_secret = os.environ.get('FEISHU_SECRET')
    github_token = os.environ.get('GITHUB_TOKEN')

    # 检查至少有一个推送渠道可用
    if not sendkey and not feishu_webhook_url:
        print("错误: 未设置任何推送渠道，请至少配置 SERVERCHAN_SENDKEY 或 FEISHU_WEBHOOK_URL")
        exit(1)
    
    # 初始化翻译器（如果配置了腾讯云密钥）
    translator = None
    if tencent_secret_id and tencent_secret_key:
        print("\n🔑 检测到腾讯云API配置，初始化翻译器...")
        translator = TencentTranslator(tencent_secret_id, tencent_secret_key)
    else:
        print("\n⚠️ 未配置腾讯云API密钥，将跳过翻译功能")
        print("   请在 GitHub Secrets 中添加 TENCENT_SECRET_ID 和 TENCENT_SECRET_KEY")
    
    # 1. 抓取新闻（国际 + 国内）
    print("\n📡 开始抓取国际AI科技资讯...\n")
    fetcher = AINewsFetcher(translator=translator)
    news = fetcher.fetch_all()
    
    print(f"\n✓ 共处理 {len(news)} 条国际AI相关新闻，{len(fetcher.chinese_news_items)} 条国内AI相关新闻\n")

    # 2. 抓取 GitHub 热门大模型/Agent 开源项目
    print("\n🐙 开始抓取 GitHub 热门大模型/Agent 开源项目...\n")
    github_fetcher = GitHubTrendingFetcher(github_token=github_token)
    fetcher.github_repos = github_fetcher.fetch_trending_repos(count=10)

    # 3. 格式化内容
    title, wechat_content = fetcher.format_for_wechat()
    feishu_payload = fetcher.format_for_feishu()

    # 4. 保存新闻到文件（维持仓库活跃，防止GitHub禁用定时任务）
    news_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    news_dir = 'news'
    os.makedirs(news_dir, exist_ok=True)
    news_file = os.path.join(news_dir, f'{news_date}.md')
    with open(news_file, 'w', encoding='utf-8') as f:
        f.write(f"# {title}\n\n")
        f.write(wechat_content)
    print(f"✓ 新闻已保存到 {news_file}")

    # 5. 推送到微信（Server酱）
    overall_success = False
    if sendkey:
        print("📤 正在推送到微信（Server酱）...")
        wechat_pusher = ServerChanPusher(sendkey)
        if wechat_pusher.push(title, wechat_content):
            overall_success = True
    else:
        print("⚠️ 未配置 SERVERCHAN_SENDKEY，跳过微信推送")

    # 6. 推送到飞书
    if feishu_webhook_url:
        print("📤 正在推送到飞书...")
        feishu_pusher = FeishuPusher(feishu_webhook_url, secret=feishu_secret)
        if feishu_pusher.push(feishu_payload):
            overall_success = True
    else:
        print("⚠️ 未配置 FEISHU_WEBHOOK_URL，跳过飞书推送")

    if overall_success:
        print("\n🎉 任务完成！")
    else:
        print("\n❌ 所有推送渠道均失败，请检查配置。")
        exit(1)


if __name__ == "__main__":
    main()
