# bot.py (适配 LLOneBot - OneBot V11)

import nonebot
import os
import asyncio
from pathlib import Path
import base64

# 导入 OneBot V11 适配器
from nonebot.adapters.onebot.v11 import Adapter as OneBotAdapter

import jmcomic

from nonebot import on_command
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.exception import FinishedException

# 导入 OneBot V11 专用模块
from nonebot.adapters.onebot.v11.bot import Bot
from nonebot.adapters.onebot.v11.event import MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.adapters.onebot.v11.message import Message
from nonebot.adapters.onebot.v11.exception import ActionFailed  # 导入 ActionFailed
from nonebot.log import logger # 引入日志模块

# --- NoneBot 初始化 ---
nonebot.init()
driver = nonebot.get_driver()
driver.register_adapter(OneBotAdapter)

# --- 加载 jmcomic 配置 和 单独读取 PDF 路径 ---
OPTION_FILE_PATH = "option.yml"
JM_OPTION = None
PDF_SAVE_DIR = r"XXXXXXXXXXXXX"  # 默认/备用路径
try:
    JM_OPTION = jmcomic.create_option_by_file(OPTION_FILE_PATH)
    logger.info(f"成功加载 jmcomic 配置对象: {OPTION_FILE_PATH}")
    
    try:
        plugins_config = JM_OPTION.get("plugins", {})
        after_album_plugins = plugins_config.get("after_album", [])
        img2pdf_kwargs = {}
        
        if after_album_plugins and isinstance(after_album_plugins, list):
            for plugin_config in after_album_plugins:
                if plugin_config.get("plugin") == "img2pdf":
                    img2pdf_kwargs = plugin_config.get("kwargs", {})
                    break

        if img2pdf_kwargs:
            configured_pdf_dir = img2pdf_kwargs.get('pdf_dir')
            if configured_pdf_dir:
                PDF_SAVE_DIR = configured_pdf_dir
            logger.info(f"PDF 存储目录 (从配置中读取): {PDF_SAVE_DIR}")
        else:
            logger.warning(f"未在 option.yml 中找到 img2pdf 插件配置，使用默认路径 {PDF_SAVE_DIR}")
            
    except Exception as e:
        logger.warning(f"解析 'option.yml' 中的 pdf_dir 配置出错: {e}，使用默认路径 {PDF_SAVE_DIR}")

    Path(PDF_SAVE_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"确认 PDF 存储目录存在: {PDF_SAVE_DIR}")
except FileNotFoundError:
    logger.critical(f"'{OPTION_FILE_PATH}' 未找到！")
except Exception as e:
    logger.critical(f"加载 '{OPTION_FILE_PATH}' 出错: {e}")

# --- 机器人核心逻辑 ---
jm_downloader = on_command("jm", aliases={"JM", "禁漫"}, priority=5, block=True)

@jm_downloader.handle()
async def handle_jm_download(bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()):
    """处理 /jm [album_id] 命令 (适配 OneBot V11, 使用 LLOneBot 的 API)"""
    if JM_OPTION is None:
        await matcher.finish("机器人 jmcomic 配置未加载或初始化失败。")

    album_id = args.extract_plain_text().strip()
    if not album_id.isdigit():
        await matcher.finish(f"ID 格式错误: '{album_id}'。")

    await matcher.send(f"✅ 收到请求 {album_id}，开始下载...")

    pdf_path_str = ""
    try:
        # 1. 下载任务 (不变)
        def download_task(aid):
            JM_OPTION.download_album(aid)

        await asyncio.to_thread(download_task, album_id)
        logger.info(f"线程任务完成: {album_id}")

        # 2. 预测路径 (不变)
        pdf_filename = f"{album_id}.pdf"
        pdf_path_str = os.path.join(PDF_SAVE_DIR, pdf_filename)
        logger.info(f"预测 PDF 路径: {pdf_path_str}")

        # 3. 检查文件并尝试发送
        if os.path.exists(pdf_path_str):
            logger.info(f"文件存在: {pdf_path_str}, 准备发送...")
            pdf_path_obj = Path(pdf_path_str)

            # --- 【修正】调用API后不处理返回值 ---
            try:
                file_abs_path_str = str(pdf_path_obj.resolve())
                pdf_name = pdf_path_obj.name 

                if isinstance(event, PrivateMessageEvent):
                    logger.info(f"目标为私聊用户: {event.user_id}, 准备上传文件...")
                    
                    # 【修改点】
                    # 直接调用 API，不接收返回值 (response)
                    # 我们假设 LLOneBot 会直接发送文件
                    await bot.call_api(
                        "upload_private_file",
                        user_id=event.user_id,
                        file=file_abs_path_str,
                        name=pdf_name 
                    )
                    
                    # 【修改点】
                    # 既然文件已经发送，我们直接结束
                    await matcher.finish(f"🎉 漫画 {album_id} 的 PDF 文件已发送！")

                elif isinstance(event, GroupMessageEvent):
                    logger.info(f"目标为群聊: {event.group_id}, 准备上传文件...")
                    
                    # 【修改点】
                    # 直接调用 API，不接收返回值 (response)
                    await bot.call_api(
                        "upload_group_file",
                        group_id=event.group_id,
                        file=file_abs_path_str,
                        name=pdf_name
                    )

                    # 【修改点】
                    # 既然文件已经发送，我们直接结束
                    await matcher.finish(f"🎉 漫画 {album_id} 的 PDF 文件已发送！")
                else:
                    await matcher.finish("❌ 当前聊天类型不支持发送文件。")
                    return

            except ActionFailed as e:
                # 这个 except 仍然是必要的，用于捕获 API 调用本身的失败
                error_msg = str(e)
                logger.error(f"Error: 调用 API 失败: {error_msg}")
                await matcher.send(f"❌ 发送文件失败：{error_msg}")
                await matcher.finish()
            except FinishedException:
                raise
            except Exception as send_error:
                # 【重要】
                # 之前的 'AttributeError' 会在这里被捕获
                # 既然我们修复了它，这个 except 现在只捕获其他未知错误
                import traceback
                logger.error(f"Error: 调用 API 时出错:\n{traceback.format_exc()}")
                await matcher.send(f"❌ 发送漫画 {album_id} 的 PDF 文件失败: {send_error}")
                await matcher.finish()

        else:  # 文件不存在
            logger.error(f"Error: 未找到文件 {pdf_filename} 在 {PDF_SAVE_DIR}")
            await matcher.finish(f"❌ 下载 {album_id} 失败：未找到 PDF 文件 {pdf_filename}。")

    except FinishedException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error: 处理 /jm {album_id} 时发生严重错误:\n{traceback.format_exc()}")
        try:
            await matcher.send(f"❌ 处理漫画 {album_id} 时发生严重错误: {e}")
        except FinishedException:
            raise
        except Exception:
            pass 
        await matcher.finish()

# --- 启动 NoneBot ---
if __name__ == "__main__":
    logger.info("准备启动 NoneBot...")
    nonebot.run()