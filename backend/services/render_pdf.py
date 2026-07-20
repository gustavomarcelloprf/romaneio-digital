import io
from datetime import datetime
from flask import render_template
from weasyprint import HTML

# --- Funções de formatação específicas para este módulo ---
# Manter estas funções aqui torna o módulo independente e robusto.

def _format_brl(value):
    """Formata um número como moeda BRL."""
    if value is None: value = 0.0
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def _format_kg(value):
    """Formata um número como peso em kg com vírgula."""
    if value is None: value = 0.0
    return f"{value:.2f}".replace('.', ',')

def _format_datetime(iso_string):
    """Formata uma data ISO para DD/MM/AAAA HH:MM."""
    if not iso_string: return ""
    try:
        dt_obj = datetime.fromisoformat(iso_string)
        return dt_obj.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return iso_string

def render_pedido_pdf(loja: dict, pedido: dict, itens: list) -> io.BytesIO:
    """
    Renderiza os dados de um pedido em um PDF usando WeasyPrint.
    """
    peso_total = sum(it.get('peso_kg', 0) for it in itens)
    subtotal = peso_total * pedido.get('preco_unitario', 0)

    context = {
        "loja": loja,
        "pedido": pedido,
        "itens": itens,
        "peso_total": peso_total,
        "subtotal": subtotal,
        "format_brl": _format_brl,
        "format_kg": _format_kg,
        "format_datetime": _format_datetime,
    }

    html_string = render_template("romaneio_template.html", **context)
    
    html_obj = HTML(string=html_string)
    pdf_bytes = html_obj.write_pdf()
    
    buffer = io.BytesIO(pdf_bytes)
    buffer.seek(0)
    return buffer

