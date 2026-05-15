import configparser
import os.path
import re
import time
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk
import requests
import urllib3
from bs4 import BeautifulSoup
import random
import json


class Course:
    """课程状态管理类"""
    def __init__(self):
        self.id = '0'
        self.class_id = '0'
        self.flag = True
        self.check_list = []
        self.class_list = []
        self.sign_start_times = {}  # 记录每个签到首次检测到的时间
        self.signed_tasks = set()  # 记录已签到成功的 class_id，用于判断未签到
        self.auto_mode = False  # 是否处于自动切换模式
        self.schedule_list = []  # 定时任务列表
        self._auto_initialized = False  # 自动模式初始化标志
        self._login_check_counter = 0  # 登录检查计数器


# 创建全局实例
course = Course()


def request_with_retry(request_func, max_retries=3):
    """
    带重试机制的网络请求包装函数（无阻塞）
    :param request_func: 返回 Response 的函数（lambda 或普通函数）
    :param max_retries: 最大重试次数
    :return: Response 对象或 None
    """
    for attempt in range(max_retries):
        try:
            response = request_func()
            return response
        except (requests.ConnectionError, requests.Timeout):
            if attempt >= max_retries - 1:
                return None
            # 不使用 sleep，直接重试
            continue
        except Exception:
            return None
    return None


def on_combo_change(event):
    className = combo_var.get()
    for i in course.class_list:
        if i["CourseName"] == className:
            course.id = i["CourseID"]
            course.class_id = i["TClassID"]


def save_schedule():
    """保存定时任务到配置文件"""
    config.read(filename)
    schedule_json = json.dumps(course.schedule_list, ensure_ascii=False)
    if not config.has_section('SCHEDULE'):
        config.add_section('SCHEDULE')
    config.set('SCHEDULE', 'tasks', schedule_json)
    with open(filename, 'w') as f:
        config.write(f)


def load_schedule():
    """从配置文件加载定时任务"""
    try:
        config.read(filename)
        if config.has_section('SCHEDULE'):
            tasks_str = config.get('SCHEDULE', 'tasks', fallback='[]')
            course.schedule_list = json.loads(tasks_str)
    except Exception:
        course.schedule_list = []


def add_schedule():
    """添加一条定时任务（时间范围）"""
    start_time_str = schedule_start_time_entry.get().strip()
    end_time_str = schedule_end_time_entry.get().strip()
    course_name = schedule_combo_var.get()

    if not start_time_str or not end_time_str:
        messagebox.showwarning("提示", "请输入开始时间和结束时间，格式如 08:00")
        return
    if not course_name:
        messagebox.showwarning("提示", "请先登录并选择课程")
        return

    # 验证时间格式
    try:
        start_time = datetime.strptime(start_time_str, "%H:%M")
        end_time = datetime.strptime(end_time_str, "%H:%M")
        if start_time >= end_time:
            messagebox.showwarning("提示", "开始时间必须早于结束时间")
            return
    except ValueError:
        messagebox.showwarning("提示", "时间格式错误，请使用 HH:MM 格式，如 08:00")
        return

    # 检查时间范围是否与已有任务重叠
    for task in course.schedule_list:
        task_start = datetime.strptime(task["start_time"], "%H:%M")
        task_end = datetime.strptime(task["end_time"], "%H:%M")
        # 检查是否重叠：新任务的开始时间 < 已有任务的结束时间 且 新任务的结束时间 > 已有任务的开始时间
        if start_time < task_end and end_time > task_start:
            messagebox.showwarning("提示", f"时间范围与已有任务重叠：{task['start_time']}-{task['end_time']} → {task['course_name']}")
            return

    # 查找对应的课程信息
    course_id = ''
    class_id = ''
    for i in course.class_list:
        if i["CourseName"] == course_name:
            course_id = i["CourseID"]
            class_id = i["TClassID"]
            break

    task = {
        "start_time": start_time_str,
        "end_time": end_time_str,
        "course_name": course_name,
        "course_id": course_id,
        "class_id": class_id
    }
    course.schedule_list.append(task)
    # 按开始时间排序
    course.schedule_list.sort(key=lambda x: x["start_time"])
    save_schedule()
    refresh_schedule_tree()
    schedule_start_time_entry.delete(0, tk.END)
    schedule_end_time_entry.delete(0, tk.END)
    text_box.insert(tk.END, f"\n已添加定时任务：{start_time_str}-{end_time_str} → {course_name}\n")
    # 自动模式下立即重新评估状态
    if course.auto_mode:
        course._auto_initialized = False
        auto_switch_and_watch()


def delete_schedule():
    """删除选中的定时任务"""
    selected = schedule_tree.selection()
    if not selected:
        messagebox.showwarning("提示", "请先选择要删除的任务")
        return
    # 修复：先收集所有要删除的 idx，倒序删除，避免 pop 后 index 偏移
    indices = sorted([schedule_tree.index(item) for item in selected], reverse=True)
    removed_list = []
    for idx in indices:
        removed_list.append(course.schedule_list.pop(idx))
    for removed in reversed(removed_list):  # 按原顺序打印日志
        text_box.insert(tk.END, f"\n已删除定时任务：{removed['start_time']}-{removed['end_time']} → {removed['course_name']}\n")
    save_schedule()
    refresh_schedule_tree()
    # 自动模式下立即重新评估状态，不等下一个轮询周期
    if course.auto_mode:
        course._auto_initialized = False  # 重置初始化标志，触发首次启动的完整显示
        auto_switch_and_watch()


def refresh_schedule_tree():
    """刷新定时任务列表显示"""
    for item in schedule_tree.get_children():
        schedule_tree.delete(item)
    for idx, task in enumerate(course.schedule_list):
        schedule_tree.insert("", tk.END, values=(
            idx + 1,
            f"{task['start_time']}-{task['end_time']}",
            task["course_name"]
        ))


def get_current_active_course():
    """
    根据当前时间获取应该监听的课程（根据时间范围判断）
    返回: (active_task, next_task, status)
        - active_task: 当前应该监听的任务（可能为 None）
        - next_task: 下一个即将监听的任务（可能为 None）
        - status: "active" | "waiting" | "done"
    """
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    current_time_obj = datetime.strptime(current_time, "%H:%M")

    if not course.schedule_list:
        return None, None, "empty"

    # 遍历所有任务，找到当前时间在哪个时间范围内
    for idx, task in enumerate(course.schedule_list):
        start_time = datetime.strptime(task["start_time"], "%H:%M")
        end_time = datetime.strptime(task["end_time"], "%H:%M")
        # 如果当前时间在 [start_time, end_time) 范围内
        if start_time <= current_time_obj < end_time:
            # 查找下一个任务
            next_task = course.schedule_list[idx + 1] if idx + 1 < len(course.schedule_list) else None
            return task, next_task, "active"

    # 如果不在任何范围内，找最近的下一个任务
    for task in course.schedule_list:
        start_time = datetime.strptime(task["start_time"], "%H:%M")
        if current_time_obj < start_time:
            return None, task, "waiting"

    # 如果所有任务都已过去
    return None, None, "done"


RECORD_SEP = "━" * 28  # 记录分隔符（粗线）

def cleanup_history(max_entries=10):
    """清理历史记录，只保留最近的 max_entries 条（按分隔符切分）"""
    all_text = history_box.get("1.0", "end-1c")
    if not all_text.strip():
        return
    records = all_text.split(RECORD_SEP)
    # 过滤掉空字符串，得到有效记录（每两条分隔线之间算一条记录）
    entries = [records[i] for i in range(1, len(records), 2) if i < len(records) and records[i].strip()]
    if len(entries) > max_entries:
        # 保留最近的 max_entries 条
        keep_entries = entries[-max_entries:]
        # 重新组装文本
        new_text = RECORD_SEP + RECORD_SEP.join(keep_entries) + RECORD_SEP
        history_box.config(state=tk.NORMAL)
        history_box.delete("1.0", "end")
        history_box.insert("1.0", new_text)
        history_box.config(state=tk.DISABLED)


def record_missed_sign(task):
    """若某任务时间结束时未签到，在历史区写入未签到提示"""
    if task["class_id"] not in course.signed_tasks:
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sep = RECORD_SEP
        entry = (
            sep + "\n"
            + "\U0001f4cc \u8bfe\u7a0b\uff1a" + task["course_name"] + "\n"
            + "\u274c \u4eca\u65e5\u672a\u7b7e\u5230\n"
            + "\U0001f550 \u76d1\u542c\u7ed3\u675f\uff1a" + now_str + "\n"
            + sep + "\n"
        )
        history_box.config(state=tk.NORMAL)
        history_box.insert(tk.END, entry)
        cleanup_history()
        history_box.see(tk.END)
        history_box.config(state=tk.DISABLED)


def auto_switch_and_watch():
    """自动切换模式：根据时间表自动切换科目并监听签到"""
    if not course.auto_mode:
        return

    if not course.schedule_list:
        text_box.insert(tk.END, f"\n{datetime.now().strftime('%H:%M:%S')} 未配置定时任务，请先添加\n")
        course.auto_mode = False
        auto_btn.config(text="▶ 开始自动监听", bg="#4CAF50")
        return

    active_task, next_task, status = get_current_active_course()

    # 首次启动时显示任务列表和当前状态
    if not course._auto_initialized:
        course._auto_initialized = True
        text_box.delete("1.0", "end")
        text_box.insert(tk.END, f"⏰ 自动模式已启动\n\n")
        text_box.insert(tk.END, f"当前时间 {datetime.now().strftime('%H:%M:%S')}\n")

        # 显示今日任务列表
        text_box.insert(tk.END, f"\n📋 今日任务列表：\n")
        for idx, t in enumerate(course.schedule_list):
            text_box.insert(tk.END, f"  {t['start_time']}-{t['end_time']}  →  {t['course_name']}\n")

        text_box.insert(tk.END, f"\n{'='*50}\n")

        # 根据状态显示不同信息
        if status == "active":
            text_box.insert(tk.END, f"✅ 当前正在监听【{active_task['course_name']}】的签到活动\n")
            text_box.insert(tk.END, f"   监听时间：{active_task['start_time']} - {active_task['end_time']}\n")
            # 显示下一个任务信息
            if next_task:
                text_box.insert(tk.END, f"\n📌 下一个任务：【{next_task['course_name']}】\n")
                text_box.insert(tk.END, f"   将在 {next_task['start_time']} 开始监听\n")
            else:
                text_box.insert(tk.END, f"\n📌 这是今日最后一个任务\n")
            text_box.insert(tk.END, f"\n")
            # 设置课程信息
            course.id = active_task["course_id"]
            course.class_id = active_task["class_id"]
            combo_var.set(active_task["course_name"])
            course.check_list = []
            course.sign_start_times = {}
            # 启动监听
            course.flag = True
            watching_sign()
        elif status == "waiting":
            text_box.insert(tk.END, f"⏳ 当前不在任何任务时间范围内\n")
            text_box.insert(tk.END, f"📌 下一个即将监听的任务：\n")
            text_box.insert(tk.END, f"   {next_task['start_time']}-{next_task['end_time']}  →  {next_task['course_name']}\n\n")
            text_box.insert(tk.END, f"将在 {next_task['start_time']} 自动开始监听\n")
        elif status == "done":
            text_box.insert(tk.END, f"📭 今日所有任务已完成\n")
        elif status == "empty":
            text_box.insert(tk.END, f"📭 未配置定时任务\n")

    # 非首次运行，检查状态变化
    else:
        if status == "active":
            # 检查是否需要切换课程
            new_course_id = active_task["course_id"]
            new_class_id = active_task["class_id"]

            if course.id != new_course_id or course.class_id != new_class_id:
                # 需要切换课程，先停止当前监听
                course.flag = False
                # 切换前找到上一个任务，用于检查是否未签到
                prev_task = next((t for t in course.schedule_list if t["class_id"] == course.class_id), None)

                # 使用 after 延迟执行切换，避免阻塞主线程
                def do_switch(prev=prev_task):
                    # 切走前检查上一门课是否未签到
                    if prev is not None:
                        record_missed_sign(prev)
                    course.id = new_course_id
                    course.class_id = new_class_id
                    # 更新下拉框显示
                    combo_var.set(active_task["course_name"])

                    text_box.delete("1.0", "end")
                    text_box.insert(tk.END, f"⏰ 自动模式 | {datetime.now().strftime('%H:%M:%S')}\n")
                    text_box.insert(tk.END, f"✅ 已切换到科目：{active_task['course_name']}\n")

                    # 显示今日任务列表
                    text_box.insert(tk.END, f"\n📋 今日任务列表：\n")
                    for t in course.schedule_list:
                        marker = " ← 当前" if t == active_task else ""
                        text_box.insert(tk.END, f"  {t['start_time']}-{t['end_time']}  →  {t['course_name']}{marker}\n")

                    text_box.insert(tk.END, f"\n{'='*50}\n")
                    text_box.insert(tk.END, f"正在监听【{active_task['course_name']}】的签到活动\n")
                    text_box.insert(tk.END, f"   监听时间：{active_task['start_time']} - {active_task['end_time']}\n")
                    # 显示下一个任务信息
                    if next_task:
                        text_box.insert(tk.END, f"\n📌 下一个任务：【{next_task['course_name']}】\n")
                        text_box.insert(tk.END, f"   将在 {next_task['start_time']} 开始监听\n")
                    else:
                        text_box.insert(tk.END, f"\n📌 这是今日最后一个任务\n")
                    text_box.insert(tk.END, f"\n")

                    course.check_list = []  # 清空已签到列表
                    course.sign_start_times = {}

                    # 启动新的监听
                    course.flag = True
                    watching_sign()

                root.after(500, do_switch)
                return  # 等待切换完成后再继续

            # 如果还没开始监听，启动监听
            if not course.flag:
                course.flag = True
                watching_sign()

        elif status == "waiting":
            # 当前不在任务范围内，停止监听，提示下一个任务
            if course.flag:
                course.flag = False
                text_box.delete("1.0", "end")
                text_box.insert(tk.END, f"⏰ 自动模式 | {datetime.now().strftime('%H:%M:%S')}\n")
                text_box.insert(tk.END, f"⏳ 当前不在任何任务时间范围内\n\n")
                text_box.insert(tk.END, f"📋 今日任务列表：\n")
                for t in course.schedule_list:
                    marker = " ← 下一个" if t == next_task else ""
                    text_box.insert(tk.END, f"  {t['start_time']}-{t['end_time']}  →  {t['course_name']}{marker}\n")
                text_box.insert(tk.END, f"\n📌 下一个即将监听的任务：\n")
                text_box.insert(tk.END, f"   {next_task['start_time']}-{next_task['end_time']}  →  {next_task['course_name']}\n\n")
                text_box.insert(tk.END, f"将在 {next_task['start_time']} 自动开始监听\n")

        elif status == "done":
            # 所有任务已完成
            if course.flag:
                course.flag = False
                # 检查最后一门课是否未签到
                last_task = next((t for t in course.schedule_list if t["class_id"] == course.class_id), None)
                if last_task:
                    record_missed_sign(last_task)
                text_box.delete("1.0", "end")
                text_box.insert(tk.END, f"⏰ 自动模式 | {datetime.now().strftime('%H:%M:%S')}\n")
                text_box.insert(tk.END, f"📭 今日所有任务已完成\n")
            # 修复：任务全部完成后不再继续调度，避免空跑
            return

    # 每5秒检查一次是否需要切换到下一个课程
    root.after(5000, auto_switch_and_watch)


def toggle_auto_mode():
    """切换自动模式开关"""
    if not course.class_list:
        messagebox.showwarning("提示", "请先登录")
        return
    if not course.schedule_list:
        messagebox.showwarning("提示", "请先添加定时任务")
        return

    if course.auto_mode:
        # 关闭自动模式
        course.auto_mode = False
        course.flag = False
        course._auto_initialized = False  # 重置初始化标志
        course.signed_tasks = set()  # 重置已签到记录
        auto_btn.config(text="▶ 开始自动监听", bg="#4CAF50")
        text_box.insert(tk.END, f"\n{datetime.now().strftime('%H:%M:%S')} 自动模式已关闭\n")
    else:
        # 开启自动模式
        course.auto_mode = True
        course.flag = False  # 先设为 False，让 auto_switch_and_watch 来启动监听
        course.check_list = []
        course.sign_start_times = {}
        course._login_check_counter = 0  # 重置登录检查计数器
        auto_btn.config(text="■ 停止自动监听", bg="#f44336")
        text_box.delete("1.0", "end")
        text_box.insert(tk.END, f"{datetime.now().strftime('%H:%M:%S')} 自动模式启动中...\n")
        auto_switch_and_watch()


def save_cookie(_x):
    config['INFO'] = {
        'cookie': _x.request.headers.get("cookie")
    }
    with open(filename, 'w') as f:
        config.write(f)


def show_login_status():
    """切换到已登录状态：隐藏链接输入框，显示已登录提示"""
    login_input_frame.pack_forget()
    login_status_frame.pack()


def show_login_input():
    """切换到未登录状态：显示链接输入框，隐藏已登录提示"""
    login_status_frame.pack_forget()
    login_input_frame.pack()


def login_link():
    link = link_entry.get()
    code = re.search(r"(?<=code=)\S{32}", link)
    if code is not None:
        x.cookies.clear()
        code = code[0]
        _r = x.get(url=host + f"/P.aspx?authtype=1&code={code}&state=1")
        save_cookie(_r)
        success = get_class_list()
        if success:
            show_login_status()
        else:
            show_login_input()
    else:
        messagebox.showerror("error", "链接有误")


def get_user_id():
    _r = x.get(url=host + "/_UserCenter/MB/index.aspx")
    if _r.status_code == 200:
        soup = BeautifulSoup(_r.text, "lxml")
        # 修复：防止 hidUID 元素不存在时 .get("value") 抛出 AttributeError
        uid_elem = soup.find(id="hidUID")
        if uid_elem is None:
            text_box.insert(tk.END, "\t获取用户ID失败，请重新登录\n")
            return None
        stu_id = uid_elem.get("value")
        return stu_id


def sign(sign_code):
    # 签到码
    if len(sign_code) == 4 and sign_code.isdigit():
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "https://www.duifene.com/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd="
        }
        params = f"action=studentcheckin&studentid={get_user_id()}&checkincode={sign_code}"
        _r = x.post(
            url=host + "/_CheckIn/CheckIn.ashx", data=params, headers=headers)
        if _r.status_code == 200:
            msg = _r.json()["msgbox"]
            text_box.insert(tk.END, f"\t{msg}\n\n")
            if msg == "签到成功！":
                return True
        return False  # 修复：签到失败或请求异常时明确返回 False
    # 二维码
    else:
        _r = x.get(url=host + "/_CheckIn/MB/QrCodeCheckOK.aspx?state=" + sign_code)
        if _r.status_code == 200:
            soup = BeautifulSoup(_r.text, "lxml")
            msg = soup.find(id="DivOK").get_text()
            if "签到成功" in msg:
                text_box.insert(tk.END, f"\t{msg}\n\n")
                return True  # 成功才返回 True
            else:
                text_box.insert(tk.END, f"\t非微信链接登录，二维码无法签到\n\n")
                return False  # 失败返回 False
        return False  # 请求失败也返回 False


def sign_location(longitude, latitude):
    longitude = str(round(float(longitude) + random.uniform(-0.000089, 0.000089), 8))
    latitude = str(round(float(latitude) + random.uniform(-0.000089, 0.000089), 8))

    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://www.duifene.com/_CheckIn/MB/CheckInStudent.aspx?moduleid=16&pasd="
    }
    params = f"action=signin&sid={get_user_id()}&longitude={longitude}&latitude={latitude}"
    _r = x.post(
        url=host + "/_CheckIn/CheckInRoomHandler.ashx", data=params, headers=headers)
    if _r.status_code == 200:
        msg = _r.json()["msgbox"]
        text_box.insert(tk.END, f"\t{msg}\n\n")
        if msg == "签到成功！":
            return True
    return False  # 修复：定位签到失败或请求异常时明确返回 False


def watching_sign():
    # 每60秒检查一次登录状态，而不是每秒
    course._login_check_counter += 1
    if course._login_check_counter >= 60:
        course._login_check_counter = 0
        if not is_login():
            return

    line_count = int(text_box.index('end-1c').split('.')[0])
    text_box.delete(f"{line_count}.0", f"{line_count}.end")
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    text_box.insert(tk.END, f"持续监控：{current_time}")  # 插入当前时间
    text_box.see(tk.END)  # 滚动到最后一行
    root.update_idletasks()  # 强制刷新界面

    try:
        # 先请求 Module.aspx 激活服务器 session 中的课程信息（每10次请求一次避免频繁）
        if course._login_check_counter % 10 == 1:
            request_with_retry(
                lambda: x.get(url=host + "/_UserCenter/MB/Module.aspx?data=" + course.id,
                               headers={"Referer": "https://www.duifene.com/_UserCenter/MB/index.aspx"}, timeout=10)
            )
        _r = request_with_retry(
            lambda: x.get(url=host + f"/_CheckIn/MB/TeachCheckIn.aspx?classid={course.class_id}&temps=0&checktype=1&isrefresh=0&timeinterval=0&roomid=0&match=", timeout=10)
        )
        if _r is None:
            text_box.insert(tk.END, f"\t网络请求失败，稍后重试")
            if course.flag:
                root.after(3000, watching_sign)  # 失败后3秒重试
            return

        if _r.status_code == 200:
            if "HFChecktype" in _r.text:
                status = False
                soup = BeautifulSoup(_r.text, "lxml")

                HFSeconds = soup.find(id="HFSeconds").get("value")
                HFChecktype = soup.find(id="HFChecktype").get("value")
                HFCheckInID = soup.find(id="HFCheckInID").get("value")
                HFClassID = soup.find(id="HFClassID").get("value")
                if course.class_id == HFClassID or course.class_id in HFClassID.split(','):
                    if HFCheckInID not in course.check_list:
                        # 记录签到首次检测到的时间
                        if HFCheckInID not in course.sign_start_times:
                            course.sign_start_times[HFCheckInID] = time.time()
                            text_box.insert(tk.END, f"\n\n{current_time} 检测到签到！签到ID：{HFCheckInID}，等待{seconds_entry.get()}秒后自动签到")

                        # 计算已等待时间
                        elapsed = int(time.time() - course.sign_start_times[HFCheckInID])
                        try:
                            wait_seconds = int(seconds_entry.get())
                        except ValueError:
                            wait_seconds = 10  # 修复：输入非数字时使用默认值 10 秒
                        remaining = wait_seconds - elapsed

                        # 数字签到
                        if HFChecktype == '1':
                            sign_code = soup.find(id="HFCheckCodeKey").get("value")
                            if sign_code is not None and elapsed >= wait_seconds:
                                text_box.insert(tk.END, f"\n{current_time} 已等待{elapsed}秒，开始签到\t签到码：{sign_code}")
                                status = sign(sign_code)
                            else:
                                text_box.insert(tk.END, f"\t签到码签到\t还需等待{remaining}秒\t签到码：{sign_code}")
                        # 二维码签到
                        elif HFChecktype == '2':
                            if HFCheckInID is not None and elapsed >= wait_seconds:
                                text_box.insert(tk.END, f"\n{current_time} 已等待{elapsed}秒，开始签到\t二维码签到")
                                status = sign(HFCheckInID)
                            else:
                                text_box.insert(tk.END, f"\t二维码签到\t还需等待{remaining}秒")
                        # 定位签到
                        elif HFChecktype == '3':
                            HFRoomLongitude = soup.find(id="HFRoomLongitude").get("value")
                            HFRoomLatitude = soup.find(id="HFRoomLatitude").get("value")
                            if HFRoomLongitude is not None and HFRoomLatitude is not None and elapsed >= wait_seconds:
                                text_box.insert(tk.END, f"\n{current_time} 已等待{elapsed}秒，开始签到\t定位签到")
                                status = sign_location(HFRoomLongitude, HFRoomLatitude)
                            else:
                                text_box.insert(tk.END, f"\t定位签到\t还需等待{remaining}秒")
                        if status:
                            course.check_list.append(HFCheckInID)
                            course.signed_tasks.add(course.class_id)  # 记录该课程已签到
                            # 获取当前课程名称
                            current_course_name = "未知"
                            for c in course.class_list:
                                if c["TClassID"] == course.class_id:
                                    current_course_name = c["CourseName"]
                                    break
                            # 签到成功记录提示
                            sign_success_msg = f"\n{'='*50}\n"
                            sign_success_msg += f"✅ 签到成功！\n"
                            sign_success_msg += f"📌 课程：{current_course_name}\n"
                            sign_success_msg += f"🕐 时间：{current_time}\n"
                            sign_success_msg += f"🆔 签到ID：{HFCheckInID}\n"
                            sign_success_msg += f"{'='*50}\n"
                            text_box.insert(tk.END, sign_success_msg)
                            text_box.see(tk.END)
                            # 同步写入签到历史区（只记录关键信息，永久保留）
                            history_box.config(state=tk.NORMAL)
                            sep = RECORD_SEP
                            entry = (
                                sep + "\n"
                                + "📌 课程：" + current_course_name + "\n"
                                + "✅ 签到成功！\n"
                                + "🕐 时间：" + current_time + "\n"
                                + sep + "\n"
                            )
                            history_box.insert(tk.END, entry)
                            cleanup_history()
                            history_box.see(tk.END)
                            history_box.config(state=tk.DISABLED)
                else:
                    text_box.insert(tk.END, f"\t 检测到非本班签到")
    except Exception as e:
        text_box.insert(tk.END, f"\t请求异常: {str(e)[:30]}")

    if course.flag:
        root.after(1000, watching_sign)


def go_sign():
    if combo.get() is None or combo.get() == '':
        messagebox.showerror("错误提示", "请先登录")
        return
    headers = {
        "Referer": "https://www.duifene.com/_UserCenter/MB/index.aspx"
    }
    _r = request_with_retry(
        lambda: x.get(url=host + "/_UserCenter/MB/Module.aspx?data=" + course.id, headers=headers)
    )
    if _r is not None and _r.status_code == 200:
        if course.id in _r.text:
            # 修复：先停止已有监听，避免多次点击叠加多个轮询链
            course.flag = False

            def _start_watch():
                text_box.delete("1.0", "end")
                soup = BeautifulSoup(_r.text, "lxml")
                CourseName = soup.find(id="CourseName").text
                text_box.insert(tk.END, f"正在监听【{CourseName}】的签到活动\n\n")
                course._login_check_counter = 0  # 重置登录检查计数器
                course.flag = True
                watching_sign()

            root.after(200, _start_watch)


def get_class_list():
    # 获取用户课程列表
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": "https://www.duifene.com/_UserCenter/PC/CenterStudent.aspx"
    }
    params = "action=getstudentcourse&classtypeid=2"
    _r = request_with_retry(
        lambda: x.post(url=host + "/_UserCenter/CourseInfo.ashx", data=params, headers=headers)
    )
    if _r is not None and _r.status_code == 200:
        _json = _r.json()
        if _json is not None:
            # 检查是否为错误响应（包含 msgbox 字段）
            if isinstance(_json, dict) and "msgbox" in _json:
                messagebox.showerror("", f"{_json['msgbox']} 请重新登录。")
                x.cookies.clear()
                return False
            # 检查是否为课程列表（应该是一个列表）
            elif isinstance(_json, list) and len(_json) > 0:
                messagebox.showinfo("提示", "登录成功")
                class_name_list = []
                for i in _json:
                    class_name_list.append(i["CourseName"])
                combo['values'] = tuple(class_name_list)
                combo.set(class_name_list[0])
                course.id = _json[0]['CourseID']
                course.class_id = _json[0]["TClassID"]
                course.class_list = _json
                return True
            else:
                messagebox.showwarning("提示", "未找到课程信息")
                return False
    return False


def is_login():
    """检查登录状态，返回 True 表示已登录"""
    headers = {
        "Referer": "https://www.duifene.com/_UserCenter/PC/CenterStudent.aspx",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }
    try:
        _r = x.get(host + "/AppCode/LoginInfo.ashx?Action=checklogin", headers=headers, timeout=10)
        if _r.status_code == 200:
            if _r.json()["msg"] == "1":
                course._network_fail_count = 0  # 请求成功，重置失败计数
                return True
            else:
                messagebox.showwarning("登录状态失效", "请重新登录账号")
                x.cookies.clear()
                course.flag = False
                show_login_input()  # 登录失效，显示链接输入框
                return False
    except Exception:
        # 修复：累计网络异常次数，连续失败超过5次才弹窗提示，避免短暂断网误报
        course._network_fail_count = getattr(course, '_network_fail_count', 0) + 1
        if course._network_fail_count >= 5:
            course._network_fail_count = 0
            messagebox.showwarning("网络异常", "连续多次无法连接服务器，请检查网络或重新登录")
        return True  # 网络异常时暂时继续监听
    return False


def init():
    try:
        if not os.path.exists(filename):
            config['INFO'] = {
                'cookie': '1=1'
            }
            with open(filename, 'w') as configfile:
                config.write(configfile)
            x.get(host)
        else:
            try:
                config.read(filename)
                cookie = config.get('INFO', 'cookie')
                cookies = {}
                for pair in cookie.split('; '):
                    key, value = pair.split('=', 1)  # 限制只分割一次，防止 value 中含有 = 号
                    cookies[key] = value
                x.cookies.update(cookies)
                # 启动时主动验证 Cookie 是否仍有效，失效立即提示，不等到签到时才发现
                if is_login():
                    if get_class_list():
                        show_login_status()
                else:
                    # is_login() 内部已弹窗并清空 Cookie，这里只需在文本框提示
                    text_box.insert(tk.END, "⚠️ 上次登录已过期，请重新登录。\n")
                    show_login_input()
            except Exception as e:
                print(f"Cookie 解析失败: {e}")  # 输出日志方便排查
    except (requests.ConnectionError, requests.Timeout):
        # 如果请求失败，则没有互联网连接
        messagebox.showwarning("网络状态", "未检测到互联网连接，请检查你的网络设置。")
        root.destroy()


if __name__ == '__main__':
    host = "https://www.duifene.com"
    UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) ' \
         'Mobile/15E148 MicroMessenger/8.0.40(0x1800282a) NetType/WIFI Language/zh_CN '
    urllib3.disable_warnings()
    x = requests.Session()
    x.headers['User-Agent'] = UA
    # x.proxies = {"https": "127.0.0.1:8888"}
    x.verify = False
    filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'duifenyi.ini')
    config = configparser.ConfigParser()

    # 创建UI
    root = tk.Tk()
    # 标题
    root.title("对分易自动签到程序")
    # 禁用窗口的调整大小
    root.resizable(False, False)

    # 左侧登录区
    login_frame = tk.Frame(root, padx=10)
    login_frame.pack(side=tk.LEFT, fill=tk.Y, pady=20)
    tk.Label(login_frame, text="微信链接登录", font=('宋体', 12, 'bold')).pack(pady=(10, 5))
    tk.Label(login_frame, text="支持二维码和签到码", font=('宋体', 9), fg="gray").pack()

    # 已登录提示（默认隐藏，登录成功后显示）
    login_status_frame = tk.Frame(login_frame)
    tk.Label(login_status_frame, text="✅ 已登录成功", font=('宋体', 11), fg="#4CAF50").pack(pady=5)

    # 未登录区域：链接输入框 + 登录按钮（默认显示）
    login_input_frame = tk.Frame(login_frame)
    tk.Label(login_input_frame, text="登录链接", font=('宋体', 10)).pack(pady=(10, 2))
    link_entry = tk.Entry(login_input_frame, font=('宋体', 11), width=18)
    link_entry.pack(pady=2)
    tk.Button(login_input_frame, text="登录", command=login_link, font=('宋体', 13), width=10).pack(pady=8)

    ttk.Separator(login_frame, orient='horizontal').pack(fill=tk.X, pady=5)
    tk.Label(login_frame, text="签到等待秒数", font=('宋体', 9)).pack()
    vcmd = (root.register(lambda s: s.isdigit() or s == ""), '%P')
    seconds_entry = tk.Entry(login_frame, font=('宋体', 11), width=6,
                             validate="key", validatecommand=vcmd)
    seconds_entry.insert(0, "10")
    seconds_entry.pack(pady=2)

    # 右侧主区域
    right_container = tk.Frame(root)
    right_container.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # === 上部：手动模式区 ===
    frame_mid = tk.Frame(right_container)
    frame_mid.pack(side=tk.TOP, fill=tk.X)
    tk.Label(frame_mid, text="选择课程").pack(side=tk.LEFT, fill=tk.BOTH, pady=(10, 0), padx=(10, 0))
    combo_var = tk.StringVar()
    combo = ttk.Combobox(frame_mid, textvariable=combo_var, state="readonly")
    combo.bind("<<ComboboxSelected>>", on_combo_change)
    combo.pack(side=tk.LEFT, padx=5, pady=10)
    btn = tk.Button(frame_mid, text="手动监听签到", command=go_sign)
    btn.pack(side=tk.LEFT, padx=5, pady=10)

    # === 中部：定时任务管理区 ===
    schedule_frame = tk.LabelFrame(right_container, text="📅 定时自动切换科目", padx=5, pady=5)
    schedule_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5, 0))

    # 添加定时任务的操作行
    add_frame = tk.Frame(schedule_frame)
    add_frame.pack(fill=tk.X, pady=(0, 5))

    tk.Label(add_frame, text="开始:").pack(side=tk.LEFT)
    schedule_start_time_entry = tk.Entry(add_frame, width=6, font=('宋体', 11))
    schedule_start_time_entry.pack(side=tk.LEFT, padx=(2, 2))
    tk.Label(add_frame, text="-").pack(side=tk.LEFT)
    schedule_end_time_entry = tk.Entry(add_frame, width=6, font=('宋体', 11))
    schedule_end_time_entry.pack(side=tk.LEFT, padx=(2, 5))
    tk.Label(add_frame, text="(HH:MM)").pack(side=tk.LEFT)

    tk.Label(add_frame, text="  科目:").pack(side=tk.LEFT)
    schedule_combo_var = tk.StringVar()
    schedule_combo = ttk.Combobox(add_frame, textvariable=schedule_combo_var, state="readonly", width=20)
    schedule_combo.pack(side=tk.LEFT, padx=(2, 5))

    add_btn = tk.Button(add_frame, text="➕ 添加", command=add_schedule, bg="#2196F3", fg="white")
    add_btn.pack(side=tk.LEFT, padx=3)
    del_btn = tk.Button(add_frame, text="🗑️ 删除选中", command=delete_schedule, bg="#ff9800", fg="white")
    del_btn.pack(side=tk.LEFT, padx=3)

    # 定时任务列表
    tree_frame = tk.Frame(schedule_frame)
    tree_frame.pack(fill=tk.X)

    schedule_tree = ttk.Treeview(tree_frame, columns=("序号", "时间范围", "科目"), show="headings", height=4)
    schedule_tree.heading("序号", text="#")
    schedule_tree.heading("时间范围", text="监听时间范围")
    schedule_tree.heading("科目", text="监听科目")
    schedule_tree.column("序号", width=30, anchor="center")
    schedule_tree.column("时间范围", width=120, anchor="center")
    schedule_tree.column("科目", width=250, anchor="w")
    schedule_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)

    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=schedule_tree.yview)
    schedule_tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 自动监听按钮
    auto_btn_frame = tk.Frame(schedule_frame)
    auto_btn_frame.pack(fill=tk.X, pady=(5, 0))
    auto_btn = tk.Button(auto_btn_frame, text="▶ 开始自动监听", command=toggle_auto_mode,
                         bg="#4CAF50", fg="white", font=('宋体', 12, 'bold'), height=1)
    auto_btn.pack(fill=tk.X)

    # === 使用说明（可折叠）===
    help_visible = tk.BooleanVar(value=False)
    help_frame_outer = tk.Frame(right_container)
    help_frame_outer.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(2, 0))

    help_text = (
        "1、打开电脑端微信，复制以下链接到文件传输助手并发送：\n"
        "https://open.weixin.qq.com/connect/oauth2/authorize?appid=wx1b5650884f657981"
        "&redirect_uri=https://www.duifene.com/_FileManage/PdfView.aspx?file=https%3A%2F%2F"
        "fs.duifene.com%2Fres%2Fr2%2Fu6106199%2F%E5%AF%B9%E5%88%86%E6%98%93%E7%99%BB%E5%BD%95"
        "_876c9d439ca68ead389c.pdf&response_type=code&scope=snsapi_userinfo&connect_redirect=1#wechat_redirect\n\n"
        "2、点击进入链接，点击微信浏览器右上角三个点，复制链接，粘贴到左侧输入框登录。"
    )

    help_content = tk.Text(help_frame_outer, height=5, font=("宋体", 9),
                           bg="#fffde7", relief=tk.FLAT, wrap=tk.WORD)
    help_content.insert(tk.END, help_text)
    help_content.config(state=tk.DISABLED)

    def toggle_help():
        if help_visible.get():
            help_content.pack_forget()
            help_toggle_btn.config(text="📖 使用方法  ▶")
            help_visible.set(False)
        else:
            help_content.pack(fill=tk.X, pady=(2, 0))
            help_toggle_btn.config(text="📖 使用方法  ▼")
            help_visible.set(True)

    help_toggle_btn = tk.Button(help_frame_outer, text="📖 使用方法  ▶",
                                command=toggle_help, anchor="w",
                                bg="#e3f2fd", relief=tk.FLAT, font=("宋体", 9, "bold"))
    help_toggle_btn.pack(fill=tk.X)

    # === 下部：监控日志 + 签到历史 ===
    frame_bottom = tk.Frame(right_container)
    frame_bottom.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)

    # 监控日志区（左侧）
    frame_log = tk.LabelFrame(frame_bottom, text="📡 监控日志", padx=3, pady=3)
    frame_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 3), pady=(5, 10))
    text_box = tk.Text(frame_log, width=55, height=15, font=('宋体', 9))
    text_box.pack(fill=tk.BOTH, expand=True)

    # 签到历史区（右侧）
    frame_history = tk.LabelFrame(frame_bottom, text="✅ 签到历史", padx=3, pady=3)
    frame_history.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(0, 5), pady=(5, 10))
    history_box = tk.Text(frame_history, width=30, height=15, font=('宋体', 9), state=tk.DISABLED)
    history_box.pack(fill=tk.BOTH, expand=True)

    # 初始化
    init()
    load_schedule()
    refresh_schedule_tree()

    # 定时同步课程列表到定时任务下拉框（登录后class_list会更新）
    def check_and_sync():
        if course.class_list:
            current_values = schedule_combo['values']
            new_names = tuple(c["CourseName"] for c in course.class_list)
            if current_values != new_names:
                schedule_combo['values'] = new_names
                if new_names and not schedule_combo_var.get():
                    schedule_combo.set(new_names[0])
        root.after(2000, check_and_sync)

    check_and_sync()

    # 所有初始化完成后，延迟刷新 UI，确保事件循环已启动
    # 解决首次运行时输入框无法点击的问题
    def finish_init():
        root.focus_force()  # 强制窗口获取焦点
        root.update_idletasks()  # 强制刷新 UI，处理所有挂起事件
        # 重新注册验证命令，确保验证机制正常工作
        vcmd_re = (root.register(lambda s: s.isdigit() or s == ""), '%P')
        seconds_entry.config(validate="none")
        seconds_entry.config(validate="key", validatecommand=vcmd_re)

    root.after(100, finish_init)
    root.mainloop()
