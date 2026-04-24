import os
import asyncio
import subprocess
import tempfile
import httpx
import cloudinary.uploader
import imageio_ffmpeg as ffmpeg
from app.core.config import logger, supabase_admin

async def process_video_queue_worker(queue: asyncio.Queue):
    """
    Sequential worker that crunches videos one-by-one.
    """
    logger.info("VIDEO_ENGINE: --- Worker Process Started ---")
    while True:
        lead_id, work_item = await queue.get()
        logger.info(f"VIDEO_ENGINE: [Task Received] Lead ID: {lead_id} | Target: {work_item}")
        
        try:
            await asyncio.sleep(1) 
            await process_video_optimized(lead_id, work_item)
            logger.info(f"VIDEO_ENGINE: [Success] Lead {lead_id} fully optimized.")
        except Exception as e:
            logger.error(f"VIDEO_ENGINE: [CRITICAL FAILURE] Lead {lead_id} failed: {str(e)}", exc_info=True)
        finally:
            queue.task_done()

async def process_video_optimized(lead_id: str, work_item: str):
    """
    Handles both URL and LOCAL files.
    """
    upload_dir = "web/static/uploads/raw"
    raw_path = None
    
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = os.path.join(tmp_dir, "compressed.mp4")

            # 1. ACQUIRE RAW FILE
            if work_item.startswith("LOCAL:"):
                temp_id = work_item.split(":")[1]
                raw_path = os.path.join(upload_dir, f"{temp_id}.mov")
                logger.info(f"VIDEO_ENGINE: [1/4 Local] Using local file: {raw_path}")
                if not os.path.exists(raw_path):
                    raise Exception(f"Local file {raw_path} not found.")
            else:
                raw_path = os.path.join(tmp_dir, "downloaded.mov")
                logger.info(f"VIDEO_ENGINE: [1/4 Download] Fetching from URL: {work_item}")
                async with httpx.AsyncClient() as client:
                    resp = await client.get(work_item, timeout=600.0)
                    with open(raw_path, "wb") as f:
                        f.write(resp.content)

            # 2. TRANSCODE (1080p, 8-bit, Ultrafast)
            ffmpeg_exe = ffmpeg.get_ffmpeg_exe()
            cmd = [
                ffmpeg_exe, "-y", "-i", raw_path,
                "-vf", "scale=-2:1080,format=yuv420p,fps=30",
                "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                out_path
            ]
            
            logger.info(f"VIDEO_ENGINE: [2/4 Transcode] Crunching to 1080p: {os.path.getsize(raw_path)/(1024*1024):.1f}MB...")
            process = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await process.communicate()

            # 3. UPLOAD SMALL VERSION TO CLOUDINARY
            logger.info(f"VIDEO_ENGINE: [3/4 Upload] Sending compressed version to Cloudinary...")
            result = cloudinary.uploader.upload(
                out_path, resource_type="video", folder="optimized_leads", public_id=f"lead_opt_{lead_id}"
            )
            optimized_url = result.get("secure_url")

            # 4. UPDATE DATABASE & CLEANUP
            if optimized_url:
                supabase_admin.table("leads").update({
                    "video_url": optimized_url, "status": "pending"
                }).eq("id", lead_id).execute()
                logger.info("VIDEO_ENGINE: [4/4 Success] Supabase updated.")
                
                # TRIGGER DELAYED TRADIE NOTIFICATION
                from app.api.leads import send_tradie_lead_alert
                send_tradie_lead_alert(lead_id)
            else:
                raise Exception("Cloudinary upload failed (no secure_url returned).")

    except Exception as e:
        logger.error(f"VIDEO_ENGINE: [PROCESS_FAILURE] Lead {lead_id}: {str(e)}")
        # If it failed, mark the lead as failed in DB so it doesn't stay 'pending' forever
        supabase_admin.table("leads").update({"status": "failed"}).eq("id", lead_id).execute()
        raise e
    finally:
        # ABSOLUTE GUARANTEE: Delete the local raw file if it was a LOCAL upload
        if work_item.startswith("LOCAL:") and raw_path and os.path.exists(raw_path):
            try:
                os.remove(raw_path)
                logger.info(f"VIDEO_ENGINE: [Cleanup] Successfully deleted raw file: {raw_path}")
            except Exception as cleanup_err:
                logger.error(f"VIDEO_ENGINE: [Cleanup Error] Failed to delete {raw_path}: {cleanup_err}")

