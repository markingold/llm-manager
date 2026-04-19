# Local Model Baseline Report

Generated: 2026-03-29T20:46:04.559375Z

## Summary

- Total runs: 80
- Total passes: 24
- Total errors: 0
- Pass rate: 0.3

## By Mode

- chat: models=8 runs=40 passes=12 errors=0 pass_rate=0.3
- small: models=8 runs=40 passes=12 errors=0 pass_rate=0.3
- intent: models=0 runs=0 passes=0 errors=0 pass_rate=0.0

## By Test

- arithmetic_739x481: runs=16 passes=0 errors=0 pass_rate=0.0
- arithmetic_order_ops: runs=16 passes=0 errors=0 pass_rate=0.0
- sentiment_label: runs=16 passes=0 errors=0 pass_rate=0.0
- trivia_capital: runs=16 passes=16 errors=0 pass_rate=1.0
- valid_json_strict: runs=16 passes=8 errors=0 pass_rate=0.5

## By Model

- chat::Gemma-3-27B-IT-EXL2-4.0bpw: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=5 passes=1 errors=0 pass_rate=0.2
- chat::Phi-4-mini-instruct: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::Qwen3.5-4B-Q6_K: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::llama3.1-8B: runs=5 passes=2 errors=0 pass_rate=0.4
- chat::llama3.1-8B_exl2_b6p5: runs=5 passes=1 errors=0 pass_rate=0.2
- chat::meta-llama__Llama-3.2-3B-Instruct: runs=5 passes=1 errors=0 pass_rate=0.2
- chat::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=5 passes=1 errors=0 pass_rate=0.2
- small::Gemma-3-27B-IT-EXL2-4.0bpw: runs=5 passes=2 errors=0 pass_rate=0.4
- small::LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2: runs=5 passes=1 errors=0 pass_rate=0.2
- small::Phi-4-mini-instruct: runs=5 passes=2 errors=0 pass_rate=0.4
- small::Qwen3.5-4B-Q6_K: runs=5 passes=2 errors=0 pass_rate=0.4
- small::llama3.1-8B: runs=5 passes=2 errors=0 pass_rate=0.4
- small::llama3.1-8B_exl2_b6p5: runs=5 passes=1 errors=0 pass_rate=0.2
- small::meta-llama__Llama-3.2-3B-Instruct: runs=5 passes=1 errors=0 pass_rate=0.2
- small::meta-llama__Llama-3.2-3B-Instruct_exl2_b6p5: runs=5 passes=1 errors=0 pass_rate=0.2

## Failure Samples

- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=arithmetic_739x481 ok=False error=None
  output: 355999
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=arithmetic_order_ops ok=False error=None
  output: 1160
- mode=chat model=Gemma-3-27B-IT-EXL2-4.0bpw test=sentiment_label ok=False error=None
  output: positive 
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=valid_json_strict ok=False error=None
  output: ```json {   "answer": "Confidence must be a number between 0 and 1. No extra text.",   "confidence": 1 } ```UIAlert    pitching baseball  baseball playing catch
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=arithmetic_739x481 ok=False error=None
  output: 57303
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=arithmetic_order_ops ok=False error=None
  output: (27*43)-(19*7)+6 = 1083 - 133 + 6 = 956 So 956.
- mode=chat model=LoneStriker_Hermes-3-Llama-3.1-8B-4.0bpw-h6-exl2 test=sentiment_label ok=False error=None
  output: The sentiment in the text "I love this product and would buy again" is positive. The sentence expresses a positive sentiment using the word "love" and the phras
- mode=chat model=Phi-4-mini-instruct test=arithmetic_739x481 ok=False error=None
  output: 147823In a small town, two friends start jogging at the same time: John's pace is half the speed of Steve's pace. They run for 1 hour every morning and end up r
- mode=chat model=Phi-4-mini-instruct test=arithmetic_order_ops ok=False error=None
  output: 1888The computation performed is as follows:  (27 * 43) = 1161 (19 * 7) = 133 1161 - 133 = 1028 1028 + 6 = 1034  The final integer is 1034. vélo wrap andar
- mode=chat model=Phi-4-mini-instruct test=sentiment_label ok=False error=None
  output: positiveThe sentiment of the text "I love this product and would buy again." is "positive.” This judgment is based on the presence of the word "love," which con
- mode=chat model=Qwen3.5-4B-Q6_K test=arithmetic_739x481 ok=False error=None
  output: 355459
- mode=chat model=Qwen3.5-4B-Q6_K test=arithmetic_order_ops ok=False error=None
  output: 1299
- mode=chat model=Qwen3.5-4B-Q6_K test=sentiment_label ok=False error=None
  output: positive
- mode=chat model=llama3.1-8B test=arithmetic_739x481 ok=False error=None
  output: result = 739 * 481 print(result)assistant  μβρίουThe final answer is 355919.
- mode=chat model=llama3.1-8B test=arithmetic_order_ops ok=False error=None
  output: (27*43)-(19*7)+6 = 1161 - 133 + 6 = 1034assistant  result = (27*43)-(19*7)+6 print(result)assistant  1034assistant  The
- mode=chat model=llama3.1-8B test=sentiment_label ok=False error=None
  output: The sentiment of the text can be labeled as positive.
- mode=chat model=llama3.1-8B_exl2_b6p5 test=valid_json_strict ok=False error=None
  output: Here is a well-formed JSON object with the required keys and the specified type for the confidence value:  ``` {   "answer": "A is A",   "confidence": 0.8 } ```
- mode=chat model=llama3.1-8B_exl2_b6p5 test=arithmetic_739x481 ok=False error=None
  output: You want me to calculate the product of 739 and 481.  739 * 481 = 355,139assistant  If you want to calculate the product of 739 and 481 again:  739 * 481 = 355,
- mode=chat model=llama3.1-8B_exl2_b6p5 test=arithmetic_order_ops ok=False error=None
  output: Compute 27*43, 19*7, and 6 is 636, 133, and 6.   636 - 133 + 6 = 509  So the final answer is 509.assistant  Here's a manual computation to confirm the result:  
- mode=chat model=llama3.1-8B_exl2_b6p5 test=sentiment_label ok=False error=None
  output: I love this product and would buy again.   Sentiment analysis: * The sentiment of this text is overall positive. * The words "love", "would buy again" indicate 
