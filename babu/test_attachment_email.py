import os
import re
import unittest
from unittest.mock import MagicMock, patch

from babu.bot import extract_text_from_document, resolve_action_params
from babu.departments import ExecutionHead
from babu.google_service import send_gmail

class TestAttachmentEmail(unittest.TestCase):
    def setUp(self):
        # Create a temp directory and a mock text file
        self.temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "babu", "temp")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.mock_txt_path = os.path.join(self.temp_dir, "test_file.txt")
        with open(self.mock_txt_path, "w", encoding="utf-8") as f:
            f.write("Hello world, this is a test attachment.")

    def tearDown(self):
        if os.path.exists(self.mock_txt_path):
            os.remove(self.mock_txt_path)

    def test_extract_text_from_document_text_file(self):
        """Test text extraction from plain text documents."""
        text = extract_text_from_document(self.mock_txt_path)
        self.assertEqual(text, "Hello world, this is a test attachment.")

    def test_extract_text_from_document_limit(self):
        """Test character limit truncation in text extraction."""
        text = extract_text_from_document(self.mock_txt_path, max_chars=10)
        self.assertTrue(text.startswith("Hello worl"))
        self.assertIn("Content Truncated", text)

    def test_extract_text_from_document_missing(self):
        """Test missing file handling."""
        text = extract_text_from_document("nonexistent_file_path.pdf")
        self.assertEqual(text, "")

    def test_resolve_action_params_attached_document(self):
        """Test that resolve_action_params resolves path from [Document Attached: <path>] pattern."""
        research_text = f"Draft an email to review the resume. [Document Attached: {self.mock_txt_path}]"
        params = {"to": "recipient@example.com", "subject": "Test Email", "image_path": "[NEEDS_RESEARCH_CONTEXT]"}
        
        resolved = resolve_action_params(params, research_text=research_text)
        self.assertEqual(resolved["image_path"], self.mock_txt_path)
        self.assertEqual(resolved["file_path"], self.mock_txt_path)

    def test_execution_head_resolve_params(self):
        """Test ExecutionHead._resolve_params mirrors resolve_action_params resolution."""
        research_text = f"Review requested for draft. [Document Attached: {self.mock_txt_path}]"
        params = {"to": "recipient@example.com", "subject": "Test Email", "image_path": "[NEEDS_RESEARCH_CONTEXT]"}
        
        resolved = ExecutionHead._resolve_params(params, research_text=research_text)
        self.assertEqual(resolved["image_path"], self.mock_txt_path)
        self.assertEqual(resolved["file_path"], self.mock_txt_path)

    @patch("babu.google_service.get_google_creds")
    @patch("googleapiclient.discovery.build")
    def test_send_gmail_builds_base64_attachment(self, mock_build, mock_get_creds):
        """Test that send_gmail successfully processes non-image attachments using base64 MIMEBase encoding."""
        mock_get_creds.return_value = MagicMock()
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        # Test sending with the mock txt attachment
        ok, msg = send_gmail(
            to="test@example.com",
            subject="Test Attachment Email",
            body="Please find the attached text file.",
            image_path=self.mock_txt_path
        )
        
        self.assertTrue(ok)
        self.assertIn("Email sent to test@example.com successfully", msg)
        
        # Verify the send method was called
        self.assertTrue(mock_service.users().messages().send.called)
        
        # Extract base64 message and decode it to verify MIME headers
        kwargs = mock_service.users().messages().send.call_args[1]
        raw_msg = kwargs["body"]["raw"]
        
        import base64
        decoded_bytes = base64.urlsafe_b64decode(raw_msg.encode("utf-8"))
        decoded_str = decoded_bytes.decode("utf-8", errors="ignore")
        
        self.assertIn("Content-Type: text/plain", decoded_str)
        self.assertIn('Content-Disposition: attachment; filename="test_file.txt"', decoded_str)
        self.assertIn("Content-Transfer-Encoding: base64", decoded_str)

if __name__ == "__main__":
    unittest.main()
