from PIL import Image

try:
    from rembg import remove
    input_path = '/home/wppjkw/workflow_news/frontend/public/logo.jpg'
    output_path = '/home/wppjkw/workflow_news/frontend/public/logo.png'
    white_bg_path = '/home/wppjkw/workflow_news/frontend/public/logo_white.png'
    input_img = Image.open(input_path)
    
    # Analyze corner pixels to guess current background
    top_left = input_img.getpixel((0,0))
    print(f"Top-left pixel color is: {top_left}")
    
    # Remove background
    out = remove(input_img)
    out.save(output_path, "PNG")
    print("Saved transparent PNG")

    # Also save one with a solid white background just in case
    white_bg = Image.new("RGBA", out.size, "WHITE")
    white_bg.paste(out, (0, 0), out)
    white_bg.convert("RGB").save(white_bg_path, "PNG")
    print("Saved white background PNG")

except Exception as e:
    print(f"Error: {e}")
