from typing import Optional, Dict, List
from .manager import DatabaseManager

class ShopModel:
    """Database abstraction layer for shop system - MySQL version"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    # ==================== SHOP MANAGEMENT ====================
    
    async def create_shop(self, shop_data: Dict) -> bool:
        """Create a new shop"""
        try:
            await self.db.execute("""
                INSERT INTO shops (shop_name, title, description, image_url, footer_text, color, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                shop_data['shop_name'], shop_data['title'], shop_data.get('description'),
                shop_data.get('image_url'), shop_data.get('footer_text'), 
                shop_data.get('color', 'orange'), shop_data.get('created_by')
            ))
            return True
        except Exception:
            return False
    
    async def get_shop(self, shop_name: str) -> Optional[Dict]:
        """Get shop configuration"""
        return await self.db.fetch_one("SELECT * FROM shops WHERE shop_name = %s", (shop_name,))
    
    async def get_all_shops(self) -> List[Dict]:
        """Get all shops"""
        return await self.db.fetch_all("SELECT * FROM shops ORDER BY created_at DESC")
    
    async def update_shop(self, shop_name: str, shop_data: Dict) -> bool:
        """Update shop configuration"""
        try:
            await self.db.execute("""
                UPDATE shops SET title = %s, description = %s, image_url = %s, 
                footer_text = %s, color = %s WHERE shop_name = %s
            """, (
                shop_data['title'], shop_data.get('description'), shop_data.get('image_url'),
                shop_data.get('footer_text'), shop_data.get('color'), shop_name
            ))
            return True
        except Exception:
            return False
    
    async def update_shop_deployment(self, shop_name: str, channel_id: int, message_id: int, thread_id: int) -> bool:
        """Update shop deployment info"""
        try:
            await self.db.execute("""
                UPDATE shops SET channel_id = %s, message_id = %s, thread_id = %s 
                WHERE shop_name = %s
            """, (channel_id, message_id, thread_id, shop_name))
            return True
        except Exception:
            return False
    
    async def delete_shop(self, shop_name: str) -> bool:
        """Delete shop and all its items"""
        try:
            result = await self.db.execute("DELETE FROM shops WHERE shop_name = %s", (shop_name,))
            return result > 0
        except Exception:
            return False
    
    # ==================== ITEM MANAGEMENT ====================
    
    async def add_item(self, shop_name: str, item_data: Dict) -> bool:
        """Add item to shop"""
        try:
            await self.db.execute("""
                INSERT INTO shop_items (shop_name, name, description, price, stock)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                shop_name, item_data['name'], item_data['description'],
                item_data['price'], item_data.get('stock', -1)
            ))
            return True
        except Exception:
            return False
    
    async def get_items(self, shop_name: str) -> List[Dict]:
        """Get all items for a shop"""
        return await self.db.fetch_all("SELECT * FROM shop_items WHERE shop_name = %s ORDER BY price ASC", (shop_name,))
    
    async def get_item(self, item_id: int) -> Optional[Dict]:
        """Get specific item"""
        return await self.db.fetch_one("SELECT * FROM shop_items WHERE id = %s", (item_id,))
    
    async def get_item_by_name(self, item_name: str) -> Optional[Dict]:
        """Get item by name"""
        return await self.db.fetch_one("SELECT * FROM shop_items WHERE name = %s", (item_name,))
    
    async def update_item(self, item_id: int, item_data: Dict) -> bool:
        """Update item"""
        try:
            await self.db.execute("""
                UPDATE shop_items SET name = %s, description = %s, price = %s, stock = %s
                WHERE id = %s
            """, (
                item_data['name'], item_data['description'], 
                item_data['price'], item_data['stock'], item_id
            ))
            return True
        except Exception:
            return False
    
    async def update_item_stock(self, item_id: int, new_stock: int) -> bool:
        """Update item stock"""
        try:
            await self.db.execute("UPDATE shop_items SET stock = %s WHERE id = %s", (new_stock, item_id))
            return True
        except Exception:
            return False
    
    async def decrease_item_stock(self, item_id: int) -> bool:
        """Decrease item stock by 1 (for purchases)"""
        try:
            result = await self.db.execute("UPDATE shop_items SET stock = stock - 1 WHERE id = %s AND stock > 0", (item_id,))
            return result > 0
        except Exception:
            return False
    
    async def delete_item(self, item_id: int) -> bool:
        """Delete item"""
        try:
            result = await self.db.execute("DELETE FROM shop_items WHERE id = %s", (item_id,))
            return result > 0
        except Exception:
            return False
    
    # ==================== UTILITY METHODS ====================
    
    async def get_available_items(self, shop_name: str) -> List[Dict]:
        """Get items that are in stock"""
        return await self.db.fetch_all("""
            SELECT * FROM shop_items 
            WHERE shop_name = %s AND stock != 0 
            ORDER BY price ASC
        """, (shop_name,))
    
    async def search_items(self, shop_name: str, query: str) -> List[Dict]:
        """Search items by name"""
        return await self.db.fetch_all("""
            SELECT * FROM shop_items 
            WHERE shop_name = %s AND name LIKE %s 
            ORDER BY name LIMIT 25
        """, (shop_name, f"%{query}%"))