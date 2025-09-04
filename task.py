import asyncio
import base64
import time
from models import SubmissionData, SubmissionType

class BountyTask:
    def __init__(self, job_id: str, logger_func=None):
        self.job_id = job_id
        self.logger_func = logger_func
        self._task = None
        self._cancelled = False
    
    async def log(self, level: str, message: str, **kwargs):
        """Helper method to log messages"""
        if self.logger_func:
            await self.logger_func(level, message, self.job_id, **kwargs)
    
    async def get_file_content(self, submission: SubmissionData):
        """Get file content directly from submission data"""
        if submission.file_data:
            try:
                # Decode base64 to get binary content
                file_content = base64.b64decode(submission.file_data)
                return file_content
            except Exception as e:
                await self.log("error", f"Error decoding base64 file data: {e}", error=str(e))
                return None
        else:
            await self.log("warning", "No file content provided in submission data")
            return None

    async def score(self, submission: SubmissionData) -> float:
        """
        Start the scoring process for the given submission.
        Returns the final score after processing.
        """
        try:
            await self.log("info", "BountyTask scoring process started")
            
            # Create the scoring task
            self._task = asyncio.create_task(self._scoring_process(submission))
            
            # Wait for completion and return score
            score = await self._task
            return score
            
        except asyncio.CancelledError:
            await self.log("warning", "Scoring task was cancelled")
            self._cancelled = True
            raise
        except Exception as e:
            await self.log("error", f"Error in scoring process: {e}", error=str(e))
            raise
    
    async def _scoring_process(self, submission: SubmissionData) -> float:
        """Internal scoring process implementation"""
        # Simulate processing time - can be interrupted by cancellation
        await self.log("info", "Sleeping for 45 seconds")
        await asyncio.sleep(45)
        await self.log("info", "Sleep completed")
        
        # Check if cancelled during processing
        if self._cancelled:
            raise asyncio.CancelledError("Task was cancelled during processing")
        
        # Scoring logic based on submission type
        if submission.submission_type == SubmissionType.TEXT:
            # Simple text scoring based on length
            if submission.content:
                score = min(len(submission.content) / 1000.0, 1.0) * 100
            else:
                score = 0.0
        elif submission.submission_type == SubmissionType.LINK:
            # Simple URL scoring (stubbed)
            if submission.content and submission.content.startswith(("http://", "https://")):
                score = 85.0  # Good URL format
            else:
                score = 30.0  # Poor URL format
        elif submission.submission_type == SubmissionType.FILE:
            # Get file content directly from submission
            file_content = await self.get_file_content(submission)
            
            if file_content:
                # Simple file scoring based on size and basic content analysis
                file_size_kb = len(file_content) / 1024
                size_score = min(file_size_kb / 100.0, 1.0) * 50  # Max 50 points for size
                
                # Basic content analysis (stubbed)
                content_score = 0
                if submission.file_info:
                    # Bonus points for certain file types
                    mime_type = submission.file_info.mime_type.lower()
                    if 'text' in mime_type or 'application/json' in mime_type:
                        content_score = 30  # Text files get bonus
                    elif 'image' in mime_type:
                        content_score = 25  # Images get moderate bonus
                    elif 'application/pdf' in mime_type:
                        content_score = 35  # PDFs get high bonus
                    else:
                        content_score = 20  # Other files get basic score
                
                score = size_score + content_score
                filename = submission.file_name or (submission.file_info.filename if submission.file_info else 'unknown')
                await self.log("info", f"Scored file: {filename} - Size: {file_size_kb:.1f}KB, Score: {score:.1f}", 
                              file_name=filename, file_size_kb=file_size_kb, score=score)
            else:
                await self.log("warning", "Could not access file content for scoring")
                score = 0.0
        else:
            score = 0.0
        
        return round(min(score, 100.0), 2)  # Cap at 100
    
    def cleanup(self):
        """
        Kill the scoring process if any and clean up the resources
        """
        try:
            self._cancelled = True
            if self._task and not self._task.done():
                self._task.cancel()
                if self.logger_func:
                    # Note: This is synchronous logging since cleanup might be called from sync context
                    print(f"[{self.job_id}] BountyTask cleanup: cancelled scoring task")
            else:
                if self.logger_func:
                    print(f"[{self.job_id}] BountyTask cleanup: no active task to cancel")
        except Exception as e:
            print(f"[{self.job_id}] Error during BountyTask cleanup: {e}")