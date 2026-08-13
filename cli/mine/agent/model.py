
import httpx
import uuid

class Model :
    def __init__(self):
        self.model     = "big-pickle"
        self.api_key   = "Bearer public"
        self.base_url  = "https://opencode.ai/zen/v1/chat/completions"
        self.uuid      = uuid.uuid4().hex[:20]
        self.headers   = {
            "Authorization": self.api_key ,
            "Content-Type": "application/json",
            "x-opencode-client": "cli",
            "x-opencode-project": "global",
            "x-opencode-request": f"msg_{self.uuid}",
            "x-opencode-session": f"ses_{self.uuid}",
            "User-Agent": "opencode/1.15.0",
        }
        self.messages  = [
            {
                "role"    : "system",
                "content" : "You are a model that help with coding, can do the other stuff only if the user insist, otherwise you dont halucinate. you can do various things as a Programming assistant, like code review, problem solving, generate code and edit..."
            }
        ]
        
        # TODO : ask the model
        
    def ask(self,question:str):
        self._add_user(question)
       
        # get the model answer
        response = httpx.post(
            self.base_url,
            headers = self.headers,
            json = {
                "model"    : self.model,
                "messages" : self.messages
            }
        )
            
        self._add_assistant(response)
            
        return response.json()
        

    def _add_user(self,q:str):
        self.messages.append({
            "role"    : "user",
            "content" : q
        })
    
    def _add_assistant(self,q:str):
        self.messages.append({
            "role"    : "assistant",
            "content" : q
        }
        )
