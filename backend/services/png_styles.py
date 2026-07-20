# backend/services/png_styles.py
"""
Módulo de Estilos para a Geração de PNG.
Funciona como um "CSS" para o nosso romaneio, centralizando
todas as variáveis de aparência.
"""
from PIL import ImageFont

# --- Cores ---
C_BG      = "#FFFFFF"  # Fundo
C_TEXT    = "#111827"  # Texto principal
C_MUTED   = "#6B7280"  # Texto secundário (cinza)
C_PRIMARY = "#111827"  # Títulos e destaques
C_LINE    = "#E5E7EB"  # Linhas divisórias

# --- Dimensões ---
W          = 1200  # Largura total da imagem
MARGIN     = 40    # Margem nas laterais
PADDING    = 8     # Espaçamento interno das células da tabela
W_CONTENT  = W - (2 * MARGIN) # Largura útil para conteúdo
LINE_H     = 34    # Altura padrão de uma linha de texto
QR_SIZE    = 200   # Tamanho do QR Code (mantido para referência futura)

# --- Fontes ---
def _font(size=18, bold=False):
    """
    Tenta carregar uma fonte TTF. Se não encontrar, usa a fonte padrão.
    O sufixo 'b' indica a versão em negrito da fonte.
    """
    font_name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(font_name, size=size)
    except IOError:
        # Fallback para a fonte padrão do Pillow
        return ImageFont.load_default()

# Fontes pré-carregadas para fácil acesso
F_H1      = _font(36, bold=True)
F_H2      = _font(24)
F_H2_B    = _font(24, bold=True)
F_BODY    = _font(18)
F_BODY_B  = _font(18, bold=True)
F_TABLE_H = _font(16, bold=True) # Fonte para o cabeçalho da tabela

