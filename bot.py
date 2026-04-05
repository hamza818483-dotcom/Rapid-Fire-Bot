import os
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd
from fpdf import FPDF
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from flask import Flask
from threading import Thread

# ==================== কনফিগারেশন ====================
TOKEN = os.environ.get('BOT_TOKEN', "YOUR_BOT_TOKEN_HERE")
DATA_FILE = "quiz_data.json"
QUIZZES_FILE = "active_quizzes.json"

# Flask app for keeping bot alive
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "🤖 Rapid Fire Quiz Bot is running!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=8080)

# ==================== ডাটা স্ট্রাকচার ====================
class Question:
    def __init__(self, data):
        self.question = data.get('questions', '')
        self.options = [data.get(f'option{i}') for i in range(1, 6) if pd.notna(data.get(f'option{i}'))]
        self.answer = data.get('answer', '')
        self.explanation = data.get('explanation', '')
        self.type = data.get('type', 1)
        self.section = data.get('section', 1)

class ActiveQuiz:
    def __init__(self, topic, channel_id, interval, questions, send_options=True):
        self.topic = topic
        self.channel_id = channel_id
        self.interval = interval
        self.questions = questions
        self.send_options = send_options
        self.current_index = 0
        self.job = None
        self.start_time = datetime.now()
        self.user_id = None

# ==================== হেলপার ফাংশন ====================
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"quizzes": {}}

def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_active_quizzes():
    if os.path.exists(QUIZZES_FILE):
        with open(QUIZZES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_active_quizzes(quizzes):
    with open(QUIZZES_FILE, 'w', encoding='utf-8') as f:
        json.dump(quizzes, f, indent=2, ensure_ascii=False)

def format_question_text(q: Question, serial: int, with_options: bool = True) -> str:
    text = f"📝 **প্রশ্ন #{serial}**\n\n{q.question}\n\n"
    if with_options and q.options:
        for i, opt in enumerate(q.options, 1):
            text += f"{chr(64+i)}. {opt}\n"
        text += "\n⏳ উত্তর দিতে 30 সেকেন্ড সময় পাবেন!"
    else:
        text += "❓ উত্তরটি কমেন্টে লিখুন"
    return text

# ==================== বট কমান্ড হ্যান্ডলার ====================
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🤖 **Rapid Fire Quiz Bot**\n\n"
        "আমি আপনার চ্যানেলে দ্রুত কুইজ পরিচালনা করতে সাহায্য করব!\n\n"
        "**কমান্ডসমূহ:**\n"
        "/rapid -t <topic> -c <channel_id> [-i <interval>] - কুইজ শুরু করুন\n"
        "/gensheet -t <topic> - উত্তরপত্র তৈরি করুন\n"
        "/cancel_rapid - চলমান কুইজ বন্ধ করুন\n"
        "/ping - বট সক্রিয় কিনা চেক করুন\n\n"
        "**এডমিন কমান্ড:**\n"
        "/restart - বট রিস্টার্ট\n"
        "/log - লগ দেখুন\n\n"
        "**কিভাবে ব্যবহার করবেন:**\n"
        "1. CSV ফাইল আপলোড করুন\n"
        "2. অপশন সহ/ছাড়া সিলেক্ট করুন\n"
        "3. /rapid কমান্ড দিন",
        parse_mode='Markdown'
    )

async def ping(update: Update, context: CallbackContext):
    await update.message.reply_text("🏓 Pong! বট সক্রিয় আছে।")

async def handle_csv(update: Update, context: CallbackContext):
    """CSV ফাইল আপলোড হ্যান্ডলার"""
    document = update.message.document
    if not document.file_name.endswith('.csv'):
        await update.message.reply_text("❌ শুধুমাত্র CSV ফাইল সমর্থিত।")
        return
    
    await update.message.reply_text("📥 CSV ফাইল প্রসেস করা হচ্ছে...")
    
    file = await context.bot.get_file(document.file_id)
    file_path = f"temp_{update.effective_user.id}_{document.file_name}"
    await file.download_to_drive(file_path)
    
    try:
        df = pd.read_csv(file_path, encoding='utf-8')
        
        questions = []
        for _, row in df.iterrows():
            q_data = {}
            for col in df.columns:
                if col in ['questions', 'answer', 'explanation', 'type', 'section']:
                    q_data[col] = str(row[col]) if pd.notna(row[col]) else ''
                elif col.startswith('option'):
                    q_data[col] = str(row[col]) if pd.notna(row[col]) else ''
            
            # options collect
            options = []
            for i in range(1, 6):
                opt_key = f'option{i}'
                if opt_key in q_data and q_data[opt_key]:
                    options.append(q_data[opt_key])
            q_data['options'] = options
            
            questions.append(Question(q_data))
        
        if len(questions) == 0:
            await update.message.reply_text("❌ CSV ফাইলে কোনো প্রশ্ন পাওয়া যায়নি!")
            return
        
        # ইউজারের ডাটা সেভ করুন
        user_data = load_data()
        user_data['quizzes'][str(update.effective_user.id)] = {
            'questions': [(q.question, q.options, q.answer, q.explanation) for q in questions],
            'upload_time': datetime.now().isoformat(),
            'total_questions': len(questions)
        }
        save_data(user_data)
        
        # কীবোর্ড বাটন তৈরি
        keyboard = [
            [InlineKeyboardButton("✅ অপশন সহ পাঠান", callback_data=f"send_with_opts_{update.effective_user.id}")],
            [InlineKeyboardButton("📝 অপশন ছাড়া পাঠান", callback_data=f"send_without_opts_{update.effective_user.id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ সফলভাবে {len(questions)}টি প্রশ্ন লোড করা হয়েছে!\n\n"
            f"**কিভাবে প্রশ্নগুলো চ্যানেলে পাঠাতে চান?**\n\n"
            f"পছন্দ করুন:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ CSV পড়তে সমস্যা: {str(e)}\n\nসঠিক ফরম্যাট নিশ্চিত করুন।")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def button_callback(update: Update, context: CallbackContext):
    """বাটন ক্লিক হ্যান্ডলার"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("send_with_opts_"):
        user_id = data.split("_")[-1]
        context.user_data['send_options'] = True
        context.user_data['stored_user_id'] = user_id
        await query.edit_message_text(
            "✅ **অপশন সহ** পাঠানো হবে।\n\n"
            "এখন `/rapid -t আপনার_টপিক -c চ্যানেল_আইডি [-i সময়]` কমান্ড দিন।\n\n"
            "উদাহরণ: `/rapid -t বাংলাদেশ -c @your_channel -i 30`",
            parse_mode='Markdown'
        )
    elif data.startswith("send_without_opts_"):
        user_id = data.split("_")[-1]
        context.user_data['send_options'] = False
        context.user_data['stored_user_id'] = user_id
        await query.edit_message_text(
            "✅ **অপশন ছাড়া** পাঠানো হবে।\n\n"
            "এখন `/rapid -t আপনার_টপিক -c চ্যানেল_আইডি [-i সময়]` কমান্ড দিন।\n\n"
            "উদাহরণ: `/rapid -t বাংলাদেশ -c @your_channel -i 30`",
            parse_mode='Markdown'
        )

async def rapid(update: Update, context: CallbackContext):
    """/rapid -t topic -c channel_id [-i interval]"""
    try:
        args = ' '.join(context.args).split()
        topic = None
        channel_id = None
        interval = 60  # ডিফল্ট 60 সেকেন্ড
        
        for i, arg in enumerate(args):
            if arg == '-t' and i+1 < len(args):
                topic = args[i+1]
            elif arg == '-c' and i+1 < len(args):
                channel_id = args[i+1]
            elif arg == '-i' and i+1 < len(args):
                interval = int(args[i+1])
        
        if not topic or not channel_id:
            await update.message.reply_text(
                "❌ **সঠিক ফরম্যাট ব্যবহার করুন:**\n\n"
                "`/rapid -t টপিক_নাম -c চ্যানেল_আইডি [-i সেকেন্ড]`\n\n"
                "উদাহরণ:\n"
                "`/rapid -t বাংলাদেশ -c @my_channel -i 30`",
                parse_mode='Markdown'
            )
            return
        
        # ইউজারের সংরক্ষিত প্রশ্ন লোড করুন
        user_data = load_data()
        user_quizzes = user_data.get('quizzes', {})
        user_id = str(update.effective_user.id)
        
        if user_id not in user_quizzes:
            await update.message.reply_text(
                "❌ **কোনো CSV ফাইল পাওয়া যায়নি!**\n\n"
                "দয়া করে আগে একটি CSV ফাইল আপলোড করুন।"
            )
            return
        
        questions_data = user_quizzes[user_id]['questions']
        questions = []
        for q_data in questions_data:
            q = Question({
                'questions': q_data[0],
                'answer': q_data[2],
                'explanation': q_data[3]
            })
            q.options = q_data[1] if len(q_data) > 1 else []
            questions.append(q)
        
        send_options = context.user_data.get('send_options', True)
        
        # চ্যানেল আইডি ফরম্যাট চেক
        if not channel_id.startswith('@') and not channel_id.startswith('-100'):
            if not channel_id.startswith('-'):
                channel_id = f"@{channel_id}"
        
        # কুইজ শুরু করুন
        active_quiz = ActiveQuiz(topic, channel_id, interval, questions, send_options)
        active_quiz.user_id = user_id
        
        # জব শিডিউল করুন
        job_queue = context.application.job_queue
        job = job_queue.run_repeating(
            send_next_question,
            interval=interval,
            first=0,
            data={
                'chat_id': update.effective_chat.id,
                'quiz': active_quiz,
                'user_id': user_id,
                'context': context
            }
        )
        active_quiz.job = job
        
        # অ্যাক্টিভ কুইজ সেভ করুন
        active_quizzes = load_active_quizzes()
        active_quizzes[str(update.effective_chat.id)] = {
            'topic': topic,
            'channel_id': channel_id,
            'interval': interval,
            'total_questions': len(questions),
            'send_options': send_options,
            'start_time': datetime.now().isoformat(),
            'user_id': user_id
        }
        save_active_quizzes(active_quizzes)
        
        await update.message.reply_text(
            f"🎯 **কুইজ শুরু!**\n\n"
            f"📚 টপিক: `{topic}`\n"
            f"📺 চ্যানেল: `{channel_id}`\n"
            f"⏱️ ব্যবধান: `{interval}` সেকেন্ড\n"
            f"❓ মোট প্রশ্ন: `{len(questions)}`\n"
            f"📝 ফরম্যাট: `{'অপশন সহ' if send_options else 'অপশন ছাড়া'}`\n\n"
            f"✅ প্রথম প্রশ্ন পাঠানো হচ্ছে...",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ ত্রুটি: {str(e)}")

async def send_next_question(context: CallbackContext):
    """পরবর্তী প্রশ্ন পাঠান"""
    job_data = context.job.data
    quiz = job_data['quiz']
    
    if quiz.current_index >= len(quiz.questions):
        # কুইজ শেষ
        await generate_solve_sheet(context, quiz)
        context.job.schedule_removal()
        return
    
    question = quiz.questions[quiz.current_index]
    text = format_question_text(question, quiz.current_index + 1, quiz.send_options)
    
    try:
        await context.bot.send_message(
            chat_id=quiz.channel_id, 
            text=text,
            parse_mode='Markdown'
        )
        quiz.current_index += 1
    except Exception as e:
        print(f"প্রশ্ন পাঠাতে সমস্যা: {e}")

async def generate_solve_sheet(context: CallbackContext, quiz: ActiveQuiz):
    """সলভ শীট PDF জেনারেট করুন"""
    try:
        pdf = FPDF()
        pdf.add_page()
        
        # হেডার
        pdf.set_font("Arial", "B", 16)
        pdf.cell(0, 10, f"Quiz Solve Sheet - {quiz.topic}", ln=True, align='C')
        pdf.set_font("Arial", "", 10)
        pdf.cell(0, 10, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True, align='C')
        pdf.ln(10)
        
        # প্রশ্ন ও উত্তর
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 10, "Questions and Answers", ln=True)
        pdf.set_font("Arial", "", 10)
        
        for i, q in enumerate(quiz.questions, 1):
            pdf.ln(5)
            pdf.set_font("Arial", "B", 10)
            # প্রশ্ন লেখা (মাল্টি লাইনের জন্য)
            pdf.multi_cell(0, 6, f"Q{i}. {q.question}")
            pdf.set_font("Arial", "", 10)
            pdf.multi_cell(0, 6, f"Answer: {q.answer}")
            if q.explanation and q.explanation != 'nan':
                pdf.multi_cell(0, 6, f"Explanation: {q.explanation}")
            pdf.cell(0, 3, "", ln=True)
        
        # PDF সেভ করুন
        pdf_filename = f"solve_sheet_{quiz.topic}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        pdf.output(pdf_filename)
        
        # ইউজারকে PDF পাঠান
        with open(pdf_filename, 'rb') as f:
            await context.bot.send_document(
                chat_id=quiz.user_id,
                document=f,
                filename=pdf_filename,
                caption=f"✅ **কুইজ সম্পন্ন!**\n\nমোট প্রশ্ন: {len(quiz.questions)}\nটপিক: {quiz.topic}\n\nউত্তরপত্র সংযুক্ত আছে।"
            )
        
        os.remove(pdf_filename)
        
    except Exception as e:
        print(f"PDF জেনারেট করতে সমস্যা: {e}")

async def gensheet(update: Update, context: CallbackContext):
    """/gensheet - কুইজের উত্তরপত্র তৈরি করুন"""
    try:
        args = context.args
        if not args:
            await update.message.reply_text(
                "❌ **ব্যবহার:** `/gensheet -t টপিক_নাম`\n\n"
                "উদাহরণ: `/gensheet -t বাংলাদেশ`",
                parse_mode='Markdown'
            )
            return
        
        topic = None
        for i, arg in enumerate(args):
            if arg == '-t' and i+1 < len(args):
                topic = args[i+1]
        
        if not topic:
            await update.message.reply_text("❌ টপিক নাম দিন: `/gensheet -t টপিক_নাম`", parse_mode='Markdown')
            return
        
        await update.message.reply_text("📝 উত্তরপত্র তৈরি হচ্ছে... দয়া করে অপেক্ষা করুন।")
        
        # অ্যাক্টিভ কুইজ থেকে ডাটা নেওয়ার চেষ্টা
        active_quizzes = load_active_quizzes()
        for chat_id, quiz_data in active_quizzes.items():
            if quiz_data.get('topic') == topic and quiz_data.get('user_id') == str(update.effective_user.id):
                # পিডিএফ জেনারেট করুন
                user_data = load_data()
                user_quizzes = user_data.get('quizzes', {})
                user_id = str(update.effective_user.id)
                
                if user_id in user_quizzes:
                    questions_data = user_quizzes[user_id]['questions']
                    questions = []
                    for q_data in questions_data:
                        q = Question({
                            'questions': q_data[0],
                            'answer': q_data[2],
                            'explanation': q_data[3]
                        })
                        q.options = q_data[1] if len(q_data) > 1 else []
                        questions.append(q)
                    
                    temp_quiz = ActiveQuiz(topic, "", 0, questions, True)
                    temp_quiz.user_id = user_id
                    await generate_solve_sheet(context, temp_quiz)
                    return
        
        await update.message.reply_text("❌ এই টপিকের কোনো সম্পন্ন কুইজ পাওয়া যায়নি!")
        
    except Exception as e:
        await update.message.reply_text(f"❌ ত্রুটি: {str(e)}")

async def cancel_rapid(update: Update, context: CallbackContext):
    """চলমান কুইজ বন্ধ করুন"""
    chat_id = str(update.effective_chat.id)
    active_quizzes = load_active_quizzes()
    
    if chat_id in active_quizzes:
        # জব বন্ধ করুন
        current_jobs = context.application.job_queue.jobs()
        for job in current_jobs:
            if job.data and job.data.get('chat_id') == chat_id:
                job.schedule_removal()
        
        del active_quizzes[chat_id]
        save_active_quizzes(active_quizzes)
        await update.message.reply_text("✅ চলমান কুইজ বন্ধ করা হয়েছে।")
    else:
        await update.message.reply_text("❌ এই চ্যাটে কোন সক্রিয় কুইজ নেই।")

async def restart(update: Update, context: CallbackContext):
    """বট রিস্টার্ট"""
    await update.message.reply_text("🔄 বট রিস্টার্ট হচ্ছে...")
    # Note: Render会自动重启

async def get_log(update: Update, context: CallbackContext):
    """লগ দেখান"""
    await update.message.reply_text("📋 বট সচল আছে। লগ দেখতে Render Dashboard দেখুন।")

# ==================== মেইন ফাংশন ====================
def main():
    # Flask thread start for keeping alive
    thread = Thread(target=run_flask)
    thread.start()
    
    # Bot start
    app = Application.builder().token(TOKEN).build()
    
    # কমান্ড হ্যান্ডলার
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("rapid", rapid))
    app.add_handler(CommandHandler("gensheet", gensheet))
    app.add_handler(CommandHandler("cancel_rapid", cancel_rapid))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("log", get_log))
    
    # ফাইল ও বাটন হ্যান্ডলার
    app.add_handler(MessageHandler(filters.Document.ALL, handle_csv))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("🤖 Rapid Fire Quiz Bot is running on Render...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
