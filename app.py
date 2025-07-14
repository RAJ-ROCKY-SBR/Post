from flask import Flask, request, jsonify, render_template
import os
import threading
import time
import requests
import uuid
import random
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

status_data = {}
task_threads = {}
stop_flags = {}
running_tasks = {}
summaries = {}

# Random User-Agent List
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (Windows NT 6.1; WOW64; rv:61.0) Gecko/20100101 Firefox/61.0',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:64.0) Gecko/20100101 Firefox/64.0'
]

# Clean comment text
def clean_comment(text):
    return text.replace('\n', ' ').strip()

# Read lines from file
def read_file_lines(path):
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

# Comment task logic
def comment_task(task_id, post_ids, first, last, comments, tokens, delay):
    i = 0
    total_posts = len(post_ids)
    total_comments = len(comments)
    token_index = 0
    valid_tokens = tokens.copy()

    while running_tasks.get(task_id) and valid_tokens:
        if stop_flags[task_id].is_set():
            print(f"[{task_id}] Stop flag received. Stopping task.")
            break

        try:
            post_id = post_ids[i % total_posts].strip()
            comment = comments[i % total_comments].strip()
            token = valid_tokens[token_index % len(valid_tokens)].strip()

            # Build comment text
            name_parts = []
            if first.strip():
                name_parts.append(first.strip())
            name_parts.append(comment)
            if last.strip():
                name_parts.append(last.strip())
            full_comment = clean_comment(" ".join(name_parts))

            # Set headers with random User-Agent
            headers = {
                'User-Agent': random.choice(USER_AGENTS)
            }

            url = f"https://graph.facebook.com/{post_id}/comments"
            params = {"access_token": token, "message": full_comment}
            res = requests.post(url, data=params, headers=headers, timeout=10)

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = {
                "post_id": post_id,
                "comment": full_comment,
                "time": timestamp
            }

            status_data.setdefault(task_id, {"logs": []})["logs"].append(log_entry)
            status_data[task_id]["full_comment"] = full_comment
            status_data[task_id]["post_id"] = post_id
            status_data[task_id]["timestamp"] = timestamp

            if res.status_code == 200:
                summaries[task_id]['success'] += 1
                print(f"[{task_id}] ✅ Success: {full_comment}")
                token_index += 1
                i += 1
                if delay > 0:
                    time.sleep(delay)
                else:
                    # Random delay between 60 seconds to 10 minutes
                    actual_delay = random.randint(60, 420)
                    print(f"[{task_id}] Random delay: {actual_delay} seconds")
                    time.sleep(actual_delay)
            else:
                error_msg = res.json().get("error", {}).get("message", "")
                summaries[task_id]['failed'] += 1
                print(f"[{task_id}] ❌ Failed: {error_msg}")

                if "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                    print(f"[{task_id}] Removing invalid token: {token[:10]}...")
                    valid_tokens.remove(token)
                    if not valid_tokens:
                        print(f"[{task_id}] No valid tokens remaining. Exiting.")
                        break
                    continue

                token_index += 1

        except Exception as e:
            summaries[task_id]['failed'] += 1
            print(f"[{task_id}] ⚠️ Exception: {str(e)}")
            token_index += 1

    print(f"[{task_id}] Task finished or no valid tokens left.")

# Thread to handle comment task
def comment_thread(task_id, token_path, comment_path, post_ids_raw, first, last, delay):
    try:
        comments = read_file_lines(comment_path)
        tokens = read_file_lines(token_path)
        post_ids = [p.strip() for p in post_ids_raw.split(',') if p.strip()]

        running_tasks[task_id] = True
        summaries[task_id] = {'success': 0, 'failed': 0}

        comment_task(task_id, post_ids, first, last, comments, tokens, delay)

    except Exception as e:
        print(f"[{task_id}] Thread Error: {str(e)}")
    finally:
        running_tasks[task_id] = False
        print(f"[{task_id}] Task completed.")

# Route for index page
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        token_file = request.files.get('token_file')
        comment_file = request.files.get('comment_file')
        post_ids = request.form.get('post_ids')
        first_name = request.form.get('first_name', '')
        last_name = request.form.get('last_name', '')

        delay_input = request.form.get('delay', '').strip()
        delay = int(delay_input) if delay_input.isdigit() and int(delay_input) > 0 else 0

        if not token_file or not comment_file or not post_ids:
            return jsonify({"error": "Missing required fields."}), 400

        task_id = str(uuid.uuid4())
        token_path = os.path.join(UPLOAD_FOLDER, f'{task_id}_tokens.txt')
        comment_path = os.path.join(UPLOAD_FOLDER, f'{task_id}_comments.txt')
        token_file.save(token_path)
        comment_file.save(comment_path)

        stop_flags[task_id] = threading.Event()
        thread = threading.Thread(
            target=comment_thread,
            args=(task_id, token_path, comment_path, post_ids, first_name, last_name, delay),
            daemon=True
        )
        thread.start()
        task_threads[task_id] = thread

        return jsonify({"message": "Commenting started", "task_id": task_id})
    return render_template("index.html")

# Route to stop a task
@app.route('/stop', methods=['POST'])
def stop():
    data = request.get_json()
    task_id = data.get("task_id")
    if not task_id:
        return jsonify({"error": "Task ID is required"}), 400

    if task_id in task_threads:
        stop_flags[task_id].set()
        running_tasks[task_id] = False
        del task_threads[task_id]
        del stop_flags[task_id]
        return jsonify({"message": f"Stopped task {task_id}"})
    return jsonify({"error": "Invalid task ID"}), 400

# Route to get status
@app.route('/status')
def status():
    task_id = request.args.get("task_id")
    if not task_id:
        return jsonify({"error": "Missing task_id"}), 400

    summary = summaries.get(task_id, {"success": 0, "failed": 0})
    logs = status_data.get(task_id, {}).get("logs", [])
    return jsonify({
        "success": summary["success"],
        "failed": summary["failed"],
        "running": running_tasks.get(task_id, False),
        "logs": logs
    })

# Ping route to keep app alive
@app.route('/ping')
def ping():
    return "pong"

# Keep the app alive
def keep_alive():
    try:
        url = os.environ.get("RENDER_EXTERNAL_URL")
        if url:
            requests.get(url + "/ping", timeout=10)
    except:
        pass

# Background scheduler to keep the app alive
scheduler = BackgroundScheduler()
scheduler.add_job(keep_alive, "interval", minutes=14)
scheduler.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
