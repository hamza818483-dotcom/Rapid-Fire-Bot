import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot token - replace with your token
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# Store active quizzes
active_quizzes: Dict[str, Dict] = {}

class QuizManager:
    def __init__(self):
        self.quizzes = active_quizzes
    
    def create_quiz(self, chat_id: str, topic: str, channel_id: str, interval: int, questions: List[Dict], headline: str):
        quiz_id = f"{chat_id}_{datetime.now().timestamp()}"
        self.quizzes[quiz_id] = {
            'chat_id': chat_id,
            'topic': topic,
            'channel_id': channel_id,
            'interval': interval,
            'questions': questions,
            'headline': headline,
            'current_index': 0,
            'start_time': datetime.now(),
            'is_active': True,
            'answers': []
        }
        return quiz_id
    
    def get_next_question(self, quiz_id: str) -> Optional[Dict]:
        quiz = self.quizzes.get(quiz_id)
        if quiz and quiz['current_index'] < len(quiz['questions']):
            question = quiz['questions'][quiz['current_index']]
            quiz['current_index'] += 1
            return question
        return None
    
    def add_answer(self, quiz_id: str, question: str, answer: str):
        quiz = self.quizzes.get(quiz_id)
        if quiz:
            quiz['answers'].append({'question': question, 'answer': answer})
    
    def complete_quiz(self, quiz_id: str) -> Optional[Dict]:
        quiz = self.quizzes.get(quiz_id)
        if quiz:
            quiz['is_active'] = False
            return quiz
        return None
    
    def is_active(self, quiz_id: str) -> bool:
        quiz = self.quizzes.get(quiz_id)
        return quiz and quiz['is_active']

class PDFGenerator:
    @staticmethod
    def generate_solve_sheet(quiz_data: Dict, user_text: str) -> str:
        filename = f"solve_sheet_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        doc = SimpleDocTemplate(filename, pagesize=A4)
        styles = getSampleStyleSheet()
        
        # Create custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#2c3e50'),
            spaceAfter=30,
            alignment=1
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=16,
            textColor=colors.HexColor('#34495e'),
            spaceAfter=12
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=8
        )
        
        story = []
        
        # Header with user text
        story.append(Paragraph(user_text, title_style))
        story.append(Spacer(1, 0.2*inch))
        story.append(Paragraph(f"Topic: {quiz_data['topic']}", heading_style))
        story.append(Paragraph(f"Date: {quiz_data['start_time'].strftime('%B %d, %Y at %H:%M')}", normal_style))
        story.append(Spacer(1, 0.3*inch))
        
        # Questions and answers table
        data = [['Sl. No.', 'Question', 'Answer']]
        for idx, qa in enumerate(quiz_data['answers'], 1):
            data.append([str(idx), qa['question'], qa['answer']])
        
        table = Table(data, colWidths=[0.8*inch, 4*inch, 2*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498db')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        story.append(table)
        doc.build(story)
        return filename

# Initialize manager
quiz_manager = QuizManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when /start is issued."""
    await update.message.reply_text(
        '👋 Welcome to Rapid Fire Quiz Bot!\n\n'
        'Available Commands:\n'
        '• /rapid -t <topic> -c <channel_id> [-i <interval>] - Start a quiz\n'
        '• /gensheet -t <topic> - Generate a solve sheet PDF\n'
        '• /cancel_rapid - Stop a running quiz\n'
        '• /ping - Check if bot is alive\n\n'
        'Admin Commands:\n'
        '• /restart - Restart the bot\n'
        '• /log - Get bot logs\n\n'
        'First, upload a CSV file with MCQs.'
    )

async def handle_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle CSV file upload."""
    if not update.message.document:
        await update.message.reply_text("Please upload a CSV file.")
        return
    
    file = await update.message.document.get_file()
    file_path = f"temp_{update.effective_user.id}.csv"
    await file.download_to_drive(file_path)
    
    try:
        df = pd.read_csv(file_path)
        required_columns = ['question', 'answer']
        
        # Check if optional options columns exist
        has_options = all(col in df.columns for col in ['option_a', 'option_b', 'option_c', 'option_d'])
        
        questions = []
        for idx, row in df.iterrows():
            question_data = {
                'question': row['question'],
                'answer': row['answer']
            }
            if has_options:
                question_data['options'] = {
                    'A': row.get('option_a', ''),
                    'B': row.get('option_b', ''),
                    'C': row.get('option_c', ''),
                    'D': row.get('option_d', '')
                }
            questions.append(question_data)
        
        context.user_data['questions'] = questions
        context.user_data['has_options'] = has_options
        
        keyboard = [
            [InlineKeyboardButton("With Options", callback_data="send_with_options")],
            [InlineKeyboardButton("Without Options", callback_data="send_without_options")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ CSV file loaded successfully!\n"
            f"Total questions: {len(questions)}\n"
            f"Options available: {'Yes' if has_options else 'No'}\n\n"
            f"How would you like to send the questions?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        await update.message.reply_text(f"Error reading CSV: {str(e)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    context.user_data['send_with_options'] = (query.data == "send_with_options")
    await query.edit_message_text(
        f"Send mode set to: {'With Options' if context.user_data['send_with_options'] else 'Without Options'}\n\n"
        f"Now use /rapid command to start the quiz.\n"
        f"Example: /rapid -t 'Science Quiz' -c '@channelusername' -i 30\n\n"
        f"Options:\n"
        f"-t: Topic name (required)\n"
        f"-c: Channel ID or username (required)\n"
        f"-i: Interval in seconds between questions (optional, default: 30)"
    )

async def rapid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a rapid-fire quiz."""
    if 'questions' not in context.user_data:
        await update.message.reply_text("Please upload a CSV file first using /rapid command or file upload.")
        return
    
    args = context.args
    topic = None
    channel_id = None
    interval = 30
    
    # Parse arguments
    for i in range(len(args)):
        if args[i] == '-t' and i + 1 < len(args):
            topic = ' '.join(args[i+1:]).strip('"\'')
            break
    
    for i in range(len(args)):
        if args[i] == '-c' and i + 1 < len(args):
            channel_id = args[i+1]
            if not channel_id.startswith('@') and not channel_id.startswith('-100'):
                channel_id = f"@{channel_id}"
            break
    
    for i in range(len(args)):
        if args[i] == '-i' and i + 1 < len(args):
            try:
                interval = int(args[i+1])
            except ValueError:
                pass
    
    if not topic or not channel_id:
        await update.message.reply_text(
            "Usage: /rapid -t 'Topic Name' -c '@channelusername' [-i 30]\n"
            "Example: /rapid -t 'Science Quiz' -c '@myquizchannel' -i 30"
        )
        return
    
    questions = context.user_data['questions']
    send_with_options = context.user_data.get('send_with_options', False)
    
    # Ask for headline
    context.user_data['quiz_params'] = {
        'topic': topic,
        'channel_id': channel_id,
        'interval': interval,
        'questions': questions,
        'send_with_options': send_with_options
    }
    
    await update.message.reply_text(
        f"📋 Quiz Configuration:\n"
        f"Topic: {topic}\n"
        f"Channel: {channel_id}\n"
        f"Interval: {interval} seconds\n"
        f"Questions: {len(questions)}\n"
        f"Send with options: {'Yes' if send_with_options else 'No'}\n\n"
        f"Please send the headline text that will appear at the beginning of the PDF."
    )
    context.user_data['awaiting_headline'] = True

async def handle_headline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle headline input."""
    if not context.user_data.get('awaiting_headline'):
        return
    
    headline = update.message.text
    quiz_params = context.user_data.get('quiz_params')
    
    if not quiz_params:
        await update.message.reply_text("Error: Quiz parameters missing. Please start over with /rapid.")
        return
    
    # Create quiz
    quiz_id = quiz_manager.create_quiz(
        str(update.effective_user.id),
        quiz_params['topic'],
        quiz_params['channel_id'],
        quiz_params['interval'],
        quiz_params['questions'],
        headline
    )
    
    context.user_data['current_quiz_id'] = quiz_id
    context.user_data['awaiting_headline'] = False
    context.user_data['send_with_options'] = quiz_params['send_with_options']
    
    await update.message.reply_text(
        f"✅ Quiz started!\n"
        f"Quiz ID: {quiz_id[:8]}...\n"
        f"Sending questions to {quiz_params['channel_id']}\n"
        f"Interval: {quiz_params['interval']} seconds\n\n"
        f"Use /cancel_rapid to stop the quiz."
    )
    
    # Start sending questions
    await send_questions(update, context, quiz_id)

async def send_questions(update: Update, context: ContextTypes.DEFAULT_TYPE, quiz_id: str):
    """Send questions at specified intervals."""
    quiz = quiz_manager.quizzes.get(quiz_id)
    if not quiz:
        return
    
    send_with_options = context.user_data.get('send_with_options', False)
    
    while quiz_manager.is_active(quiz_id):
        question_data = quiz_manager.get_next_question(quiz_id)
        if not question_data:
            # Quiz completed
            quiz_data = quiz_manager.complete_quiz(quiz_id)
            if quiz_data:
                pdf_file = PDFGenerator.generate_solve_sheet(quiz_data, quiz_data['headline'])
                await context.bot.send_document(
                    chat_id=update.effective_user.id,
                    document=open(pdf_file, 'rb'),
                    filename=f"solve_sheet_{quiz_data['topic']}.pdf",
                    caption=f"📚 Solve sheet for '{quiz_data['topic']}' quiz has been generated!"
                )
                os.remove(pdf_file)
            break
        
        # Format message
        question_num = quiz['current_index']
        message = f"**Q{question_num}:** {question_data['question']}\n\n"
        
        if send_with_options and 'options' in question_data:
            for key, value in question_data['options'].items():
                if value:
                    message += f"{key}. {value}\n"
        
        # Send to channel
        try:
            await context.bot.send_message(
                chat_id=quiz['channel_id'],
                text=message,
                parse_mode='Markdown'
            )
            quiz_manager.add_answer(quiz_id, question_data['question'], question_data['answer'])
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            await update.message.reply_text(f"Error sending to channel: {e}")
            break
        
        # Wait for next question
        await asyncio.sleep(quiz['interval'])

async def gensheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate solve sheet for a completed quiz."""
    args = context.args
    topic = None
    
    for i in range(len(args)):
        if args[i] == '-t' and i + 1 < len(args):
            topic = ' '.join(args[i+1:]).strip('"\'')
            break
    
    if not topic:
        await update.message.reply_text("Usage: /gensheet -t 'Topic Name'")
        return
    
    # Find quiz by topic
    found_quiz = None
    for quiz_id, quiz_data in quiz_manager.quizzes.items():
        if quiz_data['topic'].lower() == topic.lower() and not quiz_data['is_active']:
            found_quiz = quiz_data
            break
    
    if not found_quiz:
        await update.message.reply_text(f"No completed quiz found with topic '{topic}'")
        return
    
    # Ask for PDF header text
    context.user_data['gensheet_topic'] = topic
    context.user_data['gensheet_quiz'] = found_quiz
    await update.message.reply_text(
        f"Found quiz: {found_quiz['topic']}\n"
        f"Questions: {len(found_quiz['answers'])}\n\n"
        f"Please send the text you want to appear at the beginning of the PDF."
    )
    context.user_data['awaiting_pdf_text'] = True

async def handle_pdf_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PDF header text input."""
    if not context.user_data.get('awaiting_pdf_text'):
        return
    
    pdf_text = update.message.text
    quiz_data = context.user_data.get('gensheet_quiz')
    
    if quiz_data:
        pdf_file = PDFGenerator.generate_solve_sheet(quiz_data, pdf_text)
        await update.message.reply_document(
            document=open(pdf_file, 'rb'),
            filename=f"solve_sheet_{quiz_data['topic']}.pdf",
            caption=f"📚 Solve sheet for '{quiz_data['topic']}' quiz"
        )
        os.remove(pdf_file)
    
    context.user_data['awaiting_pdf_text'] = False
    context.user_data.pop('gensheet_quiz', None)
    context.user_data.pop('gensheet_topic', None)

async def cancel_rapid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel an active quiz."""
    quiz_id = context.user_data.get('current_quiz_id')
    if quiz_id and quiz_manager.is_active(quiz_id):
        quiz_manager.quizzes[quiz_id]['is_active'] = False
        await update.message.reply_text("✅ Quiz cancelled successfully!")
    else:
        await update.message.reply_text("No active quiz found to cancel.")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if bot is alive."""
    start_time = datetime.now()
    await update.message.reply_text("🏓 Pong! Bot is alive and running.")
    end_time = datetime.now()
    latency = (end_time - start_time).total_seconds() * 1000
    await update.message.reply_text(f"Latency: {latency:.2f}ms")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restart the bot (admin only)."""
    await update.message.reply_text("Restarting bot...")
    # Add your restart logic here
    # For now, just a placeholder

async def log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get bot logs (admin only)."""
    await update.message.reply_text("Logs functionality would go here.")

def main():
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("rapid", rapid))
    application.add_handler(CommandHandler("gensheet", gensheet))
    application.add_handler(CommandHandler("cancel_rapid", cancel_rapid))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("restart", restart))
    application.add_handler(CommandHandler("log", log))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.Document.ALL, handle_csv))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_headline))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pdf_text))
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()
