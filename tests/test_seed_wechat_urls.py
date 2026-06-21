from __future__ import annotations

from scripts.seed_wechat_urls import load_urls, parse_wechat_html


def test_parse_wechat_html_extracts_article_metadata():
    html = """
    <html>
      <head>
        <meta property="og:title" content="备用标题" />
        <meta property="og:description" content="备用摘要" />
        <meta property="og:image" content="https://example.com/fallback.jpg" />
        <script>
          var msg_title = '英蓝学术研讨会';
          var msg_desc = '本期介绍高分子加工相关学术活动';
          var msg_cdn_url = 'https://mmbiz.qpic.cn/cover.jpg';
          var nickname = '英蓝云展';
          var publish_time = '2026-06-05 12:01:00';
        </script>
      </head>
      <body>
        <div id="js_content">
          <p>这里是公众号正文内容。</p>
        </div>
      </body>
    </html>
    """

    payload = parse_wechat_html(html, "https://mp.weixin.qq.com/s/test", "默认账号")

    assert payload.title == "英蓝学术研讨会"
    assert payload.account_name == "英蓝云展"
    assert payload.summary == "本期介绍高分子加工相关学术活动"
    assert payload.image_url == "https://mmbiz.qpic.cn/cover.jpg"
    assert payload.published_at is not None
    assert payload.published_at.year == 2026
    assert "公众号正文内容" in (payload.raw_content or "")


def test_load_urls_dedupes_and_filters_non_wechat_urls(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text(
        "\n".join(
            [
                "https://mp.weixin.qq.com/s/a?utm_source=x",
                "https://mp.weixin.qq.com/s/a",
                "https://example.com/not-wechat",
                "# comment",
                "",
            ]
        ),
        encoding="utf-8",
    )

    urls = load_urls(str(path), [])

    assert urls == ["https://mp.weixin.qq.com/s/a"]
