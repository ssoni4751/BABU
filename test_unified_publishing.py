"""
test_unified_publishing.py — Unit and Integration Tests for Unified Publishing & Protocols
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Force UTF-8 encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Add paths
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "babu"))

from babu.task_engine import TaskDTO, TaskState
from babu.departments import get_department_head
from babu.google_service import execute_google_action, send_gmail
from babu.bot import resolve_action_params


class TestUnifiedPublishing(unittest.TestCase):

    def setUp(self):
        # Create a mock temporary image file to simulate a generated image
        self.temp_img_path = os.path.abspath("temp_mock_generated_image.jpg")
        with open(self.temp_img_path, "wb") as f:
            f.write(b"MOCK_IMAGE_DATA")

    def tearDown(self):
        # Clean up mock file
        if os.path.exists(self.temp_img_path):
            os.remove(self.temp_img_path)

    def test_writing_protocols_email(self):
        """Test that WritingHead applies correct Email protocol instructions to LLM."""
        writing_head = get_department_head("writing")
        
        task = TaskDTO(
            task_id="T1",
            objective="Draft a summary about solar flares",
            department="writing",
            depends_on=[],
            priority=1,
            context={"protocol": "email"}
        )

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Drafted Email content"
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}
        mock_llm.invoke.return_value = mock_response

        # Execute dispatch to check worker invocation
        res, tokens = writing_head.dispatch(task, {}, mock_llm)

        self.assertEqual(res, "Drafted Email content")
        
        # Verify LLM was called with the system message referencing the Email protocol rules
        call_args = mock_llm.invoke.call_args[0][0]
        system_prompt = call_args[0].content
        self.assertIn("WRITING PROTOCOL: Email", system_prompt)
        self.assertIn("Do NOT include citations, references, bibliographies", system_prompt)

    def test_writing_protocols_social_publishing(self):
        """Test that WritingHead applies correct Social Media Publishing instructions to LLM."""
        writing_head = get_department_head("writing")
        
        task = TaskDTO(
            task_id="T1",
            objective="Draft a post about environment day",
            department="writing",
            depends_on=[],
            priority=1,
            context={"protocol": "publishing"}
        )

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "Drafted post content #environment"
        mock_response.additional_kwargs = {}
        mock_response.response_metadata = {}
        mock_llm.invoke.return_value = mock_response

        res, tokens = writing_head.dispatch(task, {}, mock_llm)

        self.assertEqual(res, "Drafted post content #environment")
        
        # Verify LLM system prompt contained publishing instructions
        call_args = mock_llm.invoke.call_args[0][0]
        system_prompt = call_args[0].content
        self.assertIn("WRITING PROTOCOL: Publishing / Social Media Post", system_prompt)
        self.assertIn("Focus on high engagement, readability", system_prompt)

    def test_execution_parameter_auto_resolution(self):
        """Test that ExecutionHead._resolve_params resolves and auto-injects generated image path."""
        exec_head = get_department_head("execution")

        # Case 1: Param is [NEEDS_RESEARCH_CONTEXT]
        params = {"image_path": "[NEEDS_RESEARCH_CONTEXT]"}
        research_text = f"The generated image is saved at {self.temp_img_path}"
        
        resolved = exec_head._resolve_params(params, research_text)
        self.assertEqual(resolved["image_path"], self.temp_img_path)

        # Case 2: Param is not specified, but upstream path exists
        params_empty = {}
        resolved_auto = exec_head._resolve_params(params_empty, research_text)
        self.assertEqual(resolved_auto["image_path"], self.temp_img_path)
        self.assertEqual(resolved_auto["file_path"], self.temp_img_path)

    def test_bot_parameter_auto_resolution(self):
        """Test that bot.py resolve_action_params resolves generated image paths similarly."""
        params = {"file_path": "[NEEDS_RESEARCH_CONTEXT]"}
        research_text = f"Image saved at {self.temp_img_path} correctly."
        
        resolved = resolve_action_params(params, research_text)
        self.assertEqual(resolved["file_path"], self.temp_img_path)
        self.assertEqual(resolved["image_path"], self.temp_img_path)

    @patch("babu.social_media.publish_to_facebook_page")
    @patch("babu.social_media.generate_social_post_draft")
    def test_post_to_facebook_manual_bypass(self, mock_draft, mock_publish):
        """Test that post_to_facebook bypasses image-draft auto-generation if caption is manually specified."""
        mock_publish.return_value = (True, "Published successfully")
        
        # Manual query: caption specified, even if topic is set to default (like 'general')
        ok, msg = execute_google_action("post_to_facebook", {"caption": "Manual Post Hi", "topic": "general"})
        
        self.assertTrue(ok)
        self.assertEqual(msg, "Published successfully")
        
        # Verify generate_social_post_draft was NOT called
        mock_draft.assert_not_called()
        # Verify publish_to_facebook_page was called with None image path and the manual caption
        mock_publish.assert_called_once_with("", "Manual Post Hi")

    @patch("babu.google_service.get_google_creds")
    @patch("googleapiclient.discovery.build")
    def test_send_gmail_with_image_attachment(self, mock_build, mock_creds):
        """Test that send_gmail supports MIMEMultipart construction for image attachments."""
        mock_creds.return_value = MagicMock()
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        # Call send_gmail with image attachment
        ok, msg = send_gmail("mother@mail.com", "Subject", "Body Text", self.temp_img_path)
        
        self.assertTrue(ok)
        self.assertIn("Email sent", msg)
        
        # Verify that discovery.build was invoked for gmail
        mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds.return_value)
        # Verify message send execution
        mock_service.users().messages().send.assert_called_once()

    @patch("babu.bot.ChatGroq")
    def test_planner_no_cycle_for_image_generation(self, mock_chat_groq):
        """Verify that the planner does not introduce cyclic dependencies for generate_image task during post-processing."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = """
        {
          "goal": "Generate image and post to Facebook",
          "tasks": [
            {"task_id": "T1", "objective": "Fetch details", "department": "information", "depends_on": [], "priority": 1},
            {"task_id": "T2", "objective": "Generate image", "department": "execution", "action": "generate_image", "params": {"prompt": "ITR filing started"}, "depends_on": ["T1"], "priority": 2},
            {"task_id": "T3", "objective": "Draft caption", "department": "writing", "protocol": "publishing", "depends_on": ["T1", "T2"], "priority": 3},
            {"task_id": "T4", "objective": "Post to Facebook", "department": "execution", "action": "post_to_facebook", "params": {"caption": "[NEEDS_RESEARCH_CONTEXT]", "image_path": "[NEEDS_RESEARCH_CONTEXT]"}, "depends_on": ["T2", "T3"], "priority": 4},
            {"task_id": "T5", "objective": "Report success", "department": "pa", "depends_on": ["T4"], "priority": 5}
          ]
        }
        """
        mock_llm.invoke.return_value = mock_response
        mock_chat_groq.return_value = mock_llm

        from babu.planner import plan_goal
        # Run plan_goal
        graph = plan_goal("generate image and post to facebook", model_name="llama-3.1-8b-instant")
        
        # Verify that T2 (generate_image) does NOT depend on T3 (writing)
        t2_task = next(t for t in graph.tasks if t.task_id == "T2")
        self.assertNotIn("T3", t2_task.depends_on)
        self.assertEqual(t2_task.depends_on, ["T1"])
        
        # Verify that T4 (post_to_facebook) does depend on T3 (writing)
        t4_task = next(t for t in graph.tasks if t.task_id == "T4")
        self.assertIn("T3", t4_task.depends_on)


if __name__ == "__main__":
    unittest.main()
