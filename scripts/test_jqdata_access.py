from __future__ import annotations

from datetime import date, timedelta
from getpass import getpass

from jqdatasdk import auth, get_account_info, get_money_flow, get_query_count


def main() -> None:
    username = input("聚宽账号：").strip()
    password = getpass("聚宽密码（输入时不会显示）：")
    auth(username, password)
    print("账号权限：", get_account_info())
    print("剩余查询额度：", get_query_count())

    # JQData试用账号通常延迟约3个月，使用约4个月前的日期探测权限。
    # 若恰逢非交易日，count=1 会返回该日期之前最近的可用交易日。
    probe_date = (date.today() - timedelta(days=120)).isoformat()
    result = get_money_flow(
        "000001.XSHE",
        end_date=probe_date,
        count=1,
        fields=[
            "net_amount_xl",
            "net_amount_l",
            "net_amount_m",
            "net_amount_s",
        ],
    )
    print("资金流权限测试结果：")
    print(result)


if __name__ == "__main__":
    main()
