import unittest
import json
import os
import sys
import threading
import time
import http.client

# Setup paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.bot import get_telemetry_data, CURRENT_DEPT_MODEL, CURRENT_PA_MODEL

class TestModelSwitchingAndTelemetry(unittest.TestCase):

    def test_get_telemetry_data_structure(self):
        """Verify that get_telemetry_data contains the new model capability matrix payload."""
        data = get_telemetry_data(limit=10)
        self.assertIn("aggregates", data)
        self.assertIn("ledger", data)
        self.assertIn("current_dept_model", data)
        self.assertIn("current_pa_model", data)
        self.assertIn("model_matrix", data)
        
        # Verify model matrix elements
        matrix = data["model_matrix"]
        self.assertTrue(len(matrix) > 0)
        first_row = matrix[0]
        self.assertIn("model", first_row)
        self.assertIn("provider", first_row)
        self.assertIn("token_limit", first_row)
        self.assertIn("status", first_row)
        self.assertIn("avg_latency", first_row)

    def test_http_endpoints(self):
        """Test HTTP telemetry and model switching endpoints on a test health server instance."""
        from babu.bot import HealthHandler, ThreadingHTTPServer, PORT
        
        # Start server in a background thread
        test_port = 18089
        server = ThreadingHTTPServer(("127.0.0.1", test_port), HealthHandler)
        
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        time.sleep(0.5) # allow server to spin up
        
        try:
            # 1. Test GET /api/telemetry
            conn = http.client.HTTPConnection("127.0.0.1", test_port)
            conn.request("GET", "/api/telemetry")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            
            body = json.loads(resp.read().decode("utf-8"))
            self.assertIn("current_dept_model", body)
            self.assertIn("model_matrix", body)
            
            # 2. Test POST /api/models/switch
            payload = json.dumps({
                "role": "swarm",
                "model": "llama-3.1-8b-instant"
            })
            headers = {"Content-Type": "application/json"}
            conn.request("POST", "/api/models/switch", body=payload, headers=headers)
            resp2 = conn.getresponse()
            self.assertEqual(resp2.status, 200)
            
            body2 = json.loads(resp2.read().decode("utf-8"))
            self.assertEqual(body2["status"], "success")
            self.assertEqual(body2["model"], "llama-3.1-8b-instant")
            
            # Verify the global vbabuble has switched
            from babu import bot
            self.assertEqual(bot.CURRENT_DEPT_MODEL, "llama-3.1-8b-instant")
            
            # Revert model
            payload_revert = json.dumps({
                "role": "swarm",
                "model": "llama-3.3-70b-versatile"
            })
            conn.request("POST", "/api/models/switch", body=payload_revert, headers=headers)
            resp3 = conn.getresponse()
            self.assertEqual(resp3.status, 200)
            
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()

if __name__ == "__main__":
    unittest.main()
