#!/bin/bash
set -e

# Detect directory
if [ -d "/opt/TelegramBot" ]; then
    cd /opt/TelegramBot
fi

echo "📥 Pulling latest code from GitHub..."
git pull origin main

echo "⚙️ Updating .env with girls/women categories..."
python3 -c "
with open('.env', 'r') as f:
    lines = [l for l in f if not l.startswith(('KEYWORDS', '# Search', '# --- GIRLS'))]
new_kws = '''# --- GIRLS / WOMEN PRODUCT CATEGORIES (Ordered by Importance) ---
KEYWORDS=kurta set, saree, silk saree, cotton saree, women top, women kurti, women dress, bra, sunscreen spf 50, face serum, moisturizer, lipstick, kajal, earrings, necklace set, bangles, oxidised jewellery, women flats, sandals, women heels, women sneakers, sling bags, tote bags, handbags, shampoo, hair oil, hair accessories, women watch, bedsheet, home decor, kitchen organizer

KEYWORDS_MYNTRA=kurta-sets, sarees, women-tops, women-dresses, bra, sunscreen, face-serum, moisturizer, lipstick, kajal, earrings, jewellery-set, bangles, oxidised-jewellery, flats, sandals, heels, women-sneakers, handbags, sling-bags, tote-bags, shampoo, hair-oil, hair-accessories, women-watches, bedsheets, home-decor

KEYWORDS_FLIPKART=kurta set, saree, silk saree, women top, women kurti, women dress, bra, sunscreen spf 50, face serum, moisturizer, lipstick, kajal, earrings, necklace set, bangles, oxidised jewellery, women flats, sandals, women heels, women sneakers, sling bags, tote bags, handbags, shampoo, hair oil, hair accessories, women watch, bedsheet, home decor, kitchen organizer

KEYWORDS_AMAZON=kurta set for women, saree for women, women tops, women dress, bra for women, sunscreen spf 50, face serum, moisturizer for women, lipstick, kajal, earrings for women, necklace set for women, bangles for women, oxidised jewellery, flats for women, sandals for women, heels for women, sneakers for women, sling bags for women, tote bags for women, handbags for women, shampoo for women, hair oil, hair accessories for women, women watch, bedsheet, home decor, kitchen organizer
'''
with open('.env', 'w') as f:
    f.write(''.join(lines).strip() + '\n\n' + new_kws + '\n')
print('✅ .env updated successfully!')
"

echo "🔄 Restarting telegram-bot service..."
sudo systemctl restart telegram-bot

echo "🎉 Done! Bot is now running 24/7 with new Girls/Women categories."
sudo systemctl status telegram-bot --no-pager
