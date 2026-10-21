"""Gradio Blocks 조립과 "모든 데이터는 가상" 배너. 소유: 고객 접수.

화면 로직과 규칙은 src/ui/와 모듈에 두고 여기서는 Tab만 조립한다.
"""
import gradio as gr

from ui import customer, engineer, history_editor

BANNER = "모든 데이터는 가상입니다."


def build_app() -> gr.Blocks:
    with gr.Blocks(title="M-Pilot") as demo:
        gr.Markdown(f"**{BANNER}**")
        with gr.Tab("고객"):
            customer.build()
        with gr.Tab("엔지니어"):
            engineer.build()
            history_editor.build()
    return demo


if __name__ == "__main__":
    build_app().launch()
