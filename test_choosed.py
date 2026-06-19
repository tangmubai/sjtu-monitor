"""测试 cxTjxkBkkChoosedCourse 接口 — 返回当前已选课程列表。

  python test_choosed.py
  python test_choosed.py --debug
"""
import argparse
import logging

import requests

import config
from login import login

# 已选课程接口 (项目最早用过的那个,后来换成 Display 接口)
CHOOSED_URL = "https://i.sjtu.edu.cn/xsxk/tjxkbkk_cxTjxkBkkChoosedCourse.html?gnmkdm=N253519"
CHOOSED_PARAMS = {
    "xkxnm": "2026", "xkxqm": "3", "xkly": "1",
    "njdm_id": "2025",
    "zyh_id": "16FBC4936CF888F8E065F8163EE1DCCC",
    "zyfx_id": "wfx", "bh_id": "wbj", "xz": "4", "ccdm": "w",
}


def fetch_choosed(session: requests.Session) -> list[dict]:
    r = session.post(
        CHOOSED_URL,
        data=CHOOSED_PARAMS,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": config.REFERER,
            "Origin": "https://i.sjtu.edu.cn",
            "User-Agent": config.USER_AGENT,
        },
        timeout=15,
        allow_redirects=False,
    )
    ct = r.headers.get("content-type", "")
    print(f"status={r.status_code}  ct={ct}  bytes={len(r.content)}")
    if "application/json" not in ct:
        print(f"非 JSON 响应,前 300 字符: {r.text[:300]}")
        return []
    data = r.json()
    if not isinstance(data, list):
        print(f"返回不是数组: {type(data).__name__} -> {str(data)[:200]}")
        return []
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    session = requests.Session()
    login(session)
    courses = fetch_choosed(session)

    print()
    print(f"=== 当前已选课程: {len(courses)} 门 ===\n")
    print(f"{'#':<3}{'kch':<12}{'jxbmc':<28}{'kcmc':<22}{'jxb_id'}")
    print("-" * 110)
    for i, c in enumerate(courses, 1):
        print(f"{i:<3}{c.get('kch',''):<12}{c.get('jxbmc',''):<28}"
              f"{c.get('kcmc','')[:21]:<22}{c.get('jxb_id','')}")

    # 提取 PHY1262 / MATH1206 / CS0501 / PE 类的当前已选,方便填 SWAP_MAP
    print("\n=== 监控范围内当前已选 (可用于填 SWAP_MAP) ===\n")
    interesting_kchs = {"PHY1262", "MATH1206", "CS0501", "CS0502", "PE003C04", "PE003C20"}
    for c in courses:
        if c.get("kch") in interesting_kchs:
            print(f'  "{c.get("kch")}-{c.get("jxbmc","").split("-")[-1]}":  '
                  f'jxb_id = "{c.get("jxb_id")}"')


if __name__ == "__main__":
    main()
