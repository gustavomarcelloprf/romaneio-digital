# backend/services/render_png.py
import io
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# --- CONFIGURAÇÕES DE ESTILO ---
SCALE = 3
BG_COLOR = "#FFFFFF"
COLOR_PRIMARY_TEXT = "#212529"
COLOR_SECONDARY_TEXT = "#6c757d"
COLOR_TABLE_BORDER = "#dee2e6"
COLOR_ZEBRA_STRIPE = "#f8f9fa"

# Tamanhos base
FONT_SIZE_TITLE_BASE = 32
FONT_SIZE_HEADER_BASE = 14
FONT_SIZE_BODY_BASE = 13
FONT_SIZE_SMALL_BASE = 11

# --- FONTES (Usando "Outfit") ---
try:
    font_path = Path(__file__).resolve().parent
    FONT_REGULAR = ImageFont.truetype(str(font_path / "Outfit-Regular.ttf"), FONT_SIZE_BODY_BASE * SCALE)
    FONT_BOLD = ImageFont.truetype(str(font_path / "Outfit-Bold.ttf"), FONT_SIZE_BODY_BASE * SCALE)
    FONT_TITLE = ImageFont.truetype(str(font_path / "Outfit-Bold.ttf"), FONT_SIZE_TITLE_BASE * SCALE)
    FONT_HEADER = ImageFont.truetype(str(font_path / "Outfit-Regular.ttf"), FONT_SIZE_HEADER_BASE * SCALE)
    FONT_SMALL_BOLD = ImageFont.truetype(str(font_path / "Outfit-Bold.ttf"), FONT_SIZE_SMALL_BASE * SCALE)
except IOError:
    print("[AVISO] Fontes Outfit não encontradas na pasta 'services'. Usando fontes padrão.")
    FONT_REGULAR, FONT_BOLD, FONT_TITLE, FONT_HEADER, FONT_SMALL_BOLD = (ImageFont.load_default(),) * 5

def _fmt_brl(value: float) -> str:
    if value is None: value = 0.0
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def render_pedido_png(loja: dict, pedido: dict, itens: list) -> io.BytesIO:
    padding = 50 * SCALE
    width = 800 * SCALE
    
    height = padding * 2 + (120 * SCALE)
    height += 60 * SCALE
    height += len(itens) * (40 * SCALE)
    height += 150 * SCALE # Aumentado para caber o peso total
    height += 60 * SCALE

    image = Image.new("RGB", (width, height), BG_COLOR)
    draw = ImageDraw.Draw(image)

    def draw_text_right_aligned(x, y, text, font, color):
        text_width = draw.textbbox((0,0), text, font=font)[2]
        draw.text((x - text_width, y), text, fill=color, font=font)

    # --- 1. CABEÇALHO ---
    current_y = padding
    draw.text((padding, current_y), loja.get('nome', 'Nome da Loja'), fill=COLOR_PRIMARY_TEXT, font=FONT_TITLE)

    romaneio_num = f"Romaneio #{pedido.get('id', 'N/A')}"
    draw_text_right_aligned(width - padding, current_y + (10 * SCALE), romaneio_num, FONT_HEADER, COLOR_SECONDARY_TEXT)

    current_y += 55 * SCALE
    draw.text((padding, current_y), f"Data: {pedido.get('data_iso', '')}", fill=COLOR_SECONDARY_TEXT, font=FONT_REGULAR)
    
    current_y += 25 * SCALE
    draw.text((padding, current_y), f"Cliente: {pedido.get('cliente_nome', 'N/A')}", fill=COLOR_PRIMARY_TEXT, font=FONT_HEADER)

    current_y += 25 * SCALE
    preco_unit_str = _fmt_brl(pedido.get('preco_unitario', 0))
    draw.text((padding, current_y), f"Preço Unitário: {preco_unit_str} /kg", fill=COLOR_SECONDARY_TEXT, font=FONT_REGULAR)

    # <<< ADICIONADO: Exibe o nome do vendedor se ele existir >>>
    vendedor_nome = pedido.get('vendedor_nome')
    if vendedor_nome:
        current_y += 25 * SCALE
        draw.text((padding, current_y), f"Vendedor: {vendedor_nome}", fill=COLOR_SECONDARY_TEXT, font=FONT_REGULAR)

    # Continuação do espaçamento para a linha divisória
    current_y += 50 * SCALE
    draw.line([(padding, current_y), (width - padding, current_y)], fill=COLOR_TABLE_BORDER, width=1 * SCALE)
    current_y += 15 * SCALE

    # --- 2. TABELA DE ITENS ---
    COL_ITEM_X = padding
    COL_COR_X = padding + (280 * SCALE)
    COL_SUBTOTAL_X = width - padding - (150 * SCALE)
    COL_PESO_X = width - padding

    table_header_y = current_y
    draw.text((COL_ITEM_X, table_header_y), "Item", fill=COLOR_SECONDARY_TEXT, font=FONT_SMALL_BOLD)
    draw.text((COL_COR_X, table_header_y), "Cor", fill=COLOR_SECONDARY_TEXT, font=FONT_SMALL_BOLD)
    draw_text_right_aligned(COL_SUBTOTAL_X, table_header_y, "Subtotal (R$)", FONT_SMALL_BOLD, COLOR_SECONDARY_TEXT)
    draw_text_right_aligned(COL_PESO_X, table_header_y, "Peso (kg)", FONT_SMALL_BOLD, COLOR_SECONDARY_TEXT)
    current_y += 30 * SCALE

    preco_unitario = pedido.get('preco_unitario', 0)
    for i, item in enumerate(itens):
        row_y = current_y
        
        if i % 2 == 1:
             draw.rectangle([(padding, row_y - (10 * SCALE)), (width - padding, row_y + (30 * SCALE))], fill=COLOR_ZEBRA_STRIPE, width=0)

        item_subtotal = item.get('peso_kg', 0) * preco_unitario

        draw.text((COL_ITEM_X, row_y), pedido.get('tecido', 'N/A'), fill=COLOR_PRIMARY_TEXT, font=FONT_REGULAR)
        draw.text((COL_COR_X, row_y), item.get('cor', 'N/A'), fill=COLOR_PRIMARY_TEXT, font=FONT_REGULAR)
        draw_text_right_aligned(COL_SUBTOTAL_X, row_y, _fmt_brl(item_subtotal), FONT_REGULAR, COLOR_PRIMARY_TEXT)
        draw_text_right_aligned(COL_PESO_X, row_y, f"{item.get('peso_kg', 0):,.2f}".replace('.',','), FONT_REGULAR, COLOR_PRIMARY_TEXT)
        current_y += 40 * SCALE

    current_y += 20 * SCALE
    draw.line([(padding, current_y), (width - padding, current_y)], fill=COLOR_TABLE_BORDER, width=1 * SCALE)
    current_y += 20 * SCALE
    
    # --- 3. TOTAIS ---
    # <<< NOVO: Cálculo do peso total >>>
    peso_total = sum(it.get('peso_kg', 0) for it in itens)
    
    total_desconto = pedido.get('desconto', 0.0)
    total_subtotal = sum(it.get('peso_kg', 0) * preco_unitario for it in itens)
    total_final = pedido.get('total', total_subtotal - total_desconto)

    # <<< NOVO: Exibição do peso total >>>
    draw_text_right_aligned(width - padding, current_y, f"Peso Total: {peso_total:,.2f} kg".replace('.',','), FONT_REGULAR, COLOR_SECONDARY_TEXT)
    current_y += 25 * SCALE
    
    draw_text_right_aligned(width - padding, current_y, f"Subtotal: {_fmt_brl(total_subtotal)}", FONT_REGULAR, COLOR_SECONDARY_TEXT)
    current_y += 25 * SCALE
    
    draw_text_right_aligned(width - padding, current_y, f"Desconto: {_fmt_brl(total_desconto)}", FONT_REGULAR, COLOR_SECONDARY_TEXT)
    current_y += 35 * SCALE
    
    draw_text_right_aligned(width - padding, current_y, f"Total: {_fmt_brl(total_final)}", FONT_BOLD, COLOR_PRIMARY_TEXT)

    # --- 4. RODAPÉ (PIX) ---
    if loja.get('pix_chave'):
        footer_y = height - padding - (40 * SCALE)
        draw.line([(padding, footer_y), (width - padding, footer_y)], fill=COLOR_TABLE_BORDER, width=1 * SCALE)
        footer_y += 15 * SCALE
        draw.text((padding, footer_y), "Pagamento via PIX", fill=COLOR_SECONDARY_TEXT, font=FONT_SMALL_BOLD)
        footer_y += 15 * SCALE
        
        # <<< ALTERADO: Usando FONT_BOLD para a chave PIX >>>
        draw.text((padding, footer_y), f"Chave: {loja.get('pix_chave')}", fill=COLOR_PRIMARY_TEXT, font=FONT_BOLD)

    # --- FINALIZAÇÃO ---
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(72 * SCALE, 72 * SCALE))
    buffer.seek(0)
    return buffer