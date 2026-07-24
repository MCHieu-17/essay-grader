import numpy as np
from unsloth import FastLanguageModel
import torch
import torch
import json
import os
from transformers import AutoTokenizer
from peft import PeftModel
from xgboost import XGBRFRegressor
from pydantic import BaseModel

class Settings(BaseModel):
    BASE_MODEL_NAME: str = "Qwen/Qwen3-4B"
    FINTUNED_MODEL_NAME: str = "HieuNg05/lora_qwen4b_full"
    SYSTEM_PROMPT: str = (
        "You are an EFL writing assessor for rubric-based scoring.\n"
        "For EACH criterion, propose TWO score options:\n"
        "- strict: conservative/harsh scoring (only give high scores if clearly justified)\n"
        "- lenient: generous scoring (give benefit of the doubt if partially supported)\n\n"
        "Criteria:\n"
        "1) content\n"
        "2) organization\n"
        "3) language\n\n"
        "Each score must be in {1.0, 1.5, ..., 5.0}.\n"
        "Return ONLY a JSON object with schema:\n"
        "{"
        "\"content\": {\"strict\": <number>, \"lenient\": <number>}, "
        "\"organization\": {\"strict\": <number>, \"lenient\": <number>}, "
        "\"language\": {\"strict\": <number>, \"lenient\": <number>}"
        "}\n"
        "No extra text."
    )
    MAX_LENGTH: int = 4096
    JUDGE_PATH: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "weights", "judge.ubj")

class ScoringPipline:
    def __init__(self):
        self.config = Settings()
        # Load model chấm điểm
        self.model, self.tokenizer = self._load_model()
        # Load model Judge
        self.judge = self._load_judge()

    def _load_model(self):
        base_model, base_tokenizer = FastLanguageModel.from_pretrained(
            model_name=self.config.BASE_MODEL_NAME,
            max_seq_length=self.config.MAX_LENGTH,
            dtype=torch.float16,
            load_in_4bit=False,
            load_in_16bit=True
        )
        tokenizer = AutoTokenizer.from_pretrained(self.config.FINTUNED_MODEL_NAME)
        base_model.resize_token_embeddings(len(tokenizer))

        model = PeftModel.from_pretrained(
            base_model,
            self.config.FINTUNED_MODEL_NAME,
        )

        return model, tokenizer
    
    def _format_input(self, prompt, essay):
        user_content = f"Prompt: {prompt}\n\nEssay: {essay}"
        messages = [
            {"role": "system", "content": self.config.SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ]

        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    
    def _score(self, messages):
        with torch.no_grad():
            inputs = self.tokenizer(
                messages,
                return_tensors="pt",
                truncation=True,
                max_length=self.config.MAX_LENGTH
            )
            inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
            input_len = inputs["input_ids"].shape[1]
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=128,  # Tăng lên để đủ chỗ cho JSON
                do_sample=False,
                temperature=None,
                top_p=None,
            )
            phase1_score = self.tokenizer.decode(outputs[0][input_len:], skip_special_tokens=True)

            return phase1_score
        
    def _load_judge(self):
        judge = XGBRFRegressor()
        judge.load_model(self.config.JUDGE_PATH)
        return judge
    
    def _extract_score(self, phase1_score):
        data = json.loads(phase1_score)
        
        # Cố định danh sách keys để đảm bảo thứ tự features khi đưa vào XGBoost luôn đồng nhất
        categories = ["content", "organization", "language"]
        sub_keys = ["strict", "lenient"]
        
        # Lặp qua từng category và trích xuất cả 2 giá trị
        scores = [float(data[cat][sub]) for cat in categories for sub in sub_keys]
        
        return np.array([scores])
     
    def predict(self, prompt, essay):
        # Format lại để đưa vào llm 
        messages = self._format_input(prompt, essay)
        # Dùng llm chấm điểm
        phase1_score = self._score(messages)
        # Trích xuất điểm (2 loại lenient và strict)
        extracted_score = self._extract_score(phase1_score)
        # Dùng judge để đưa ra điểm cuối cùng
        final_score = self.judge.predict(extracted_score)

        return np.round(final_score[0] * 2) / 2 # [conetent, language, organization]