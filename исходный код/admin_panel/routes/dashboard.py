# dashboard.py
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required
from datetime import datetime, timedelta
from collections import defaultdict
import numpy as np
import pytz

from admin_panel.bot_manager import bot_manager  # Импортируем менеджер бота
from admin_panel.db import orders_col, db

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def dashboard():
    # Устанавливаем период по умолчанию (последние 30 дней)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    return render_template(
        "dashboard.html",
        default_start=start_date.strftime("%Y-%m-%d"),
        default_end=end_date.strftime("%Y-%m-%d")
    )


def calculate_rfm_metrics(customer_orders, analysis_date):
    """Рассчитывает RFM метрики для клиента"""
    if not customer_orders:
        return None

    # Recency: дней с последнего заказа
    last_order_date = max(order['created_at'] for order in customer_orders)
    recency = (analysis_date - last_order_date).days

    # Frequency: количество заказов
    frequency = len(customer_orders)

    # Monetary: общая сумма заказов
    monetary = sum(order.get('total', 0) for order in customer_orders)

    # Средний чек
    avg_order_value = monetary / frequency if frequency > 0 else 0

    # Количество товаров
    total_items = sum(
        sum(item.get('quantity', 1) for item in order.get('items', []))
        for order in customer_orders
    )

    return {
        'recency': recency,
        'frequency': frequency,
        'monetary': monetary,
        'avg_order_value': avg_order_value,
        'total_items': total_items,
        'last_order_date': last_order_date,
        'first_order_date': min(order['created_at'] for order in customer_orders),
        'order_dates': [order['created_at'] for order in customer_orders]
    }


def segment_rfm_customers(customers_data):
    """Сегментирует клиентов по RFM"""
    if not customers_data:
        return [], []

    # Подготавливаем данные для квантилей
    recency_values = [c['recency'] for c in customers_data.values()]
    frequency_values = [c['frequency'] for c in customers_data.values()]
    monetary_values = [c['monetary'] for c in customers_data.values()]

    # Рассчитываем квантили (4 группы для каждого параметра)
    recency_quantiles = np.percentile(recency_values, [25, 50, 75])
    frequency_quantiles = np.percentile(frequency_values, [25, 50, 75])
    monetary_quantiles = np.percentile(monetary_values, [25, 50, 75])

    # RFM сегменты
    segments = {
        'champions': {'min_r': 1, 'max_r': 2, 'min_f': 4, 'max_f': 4, 'min_m': 4, 'max_m': 4},
        'loyal_customers': {'min_r': 1, 'max_r': 3, 'min_f': 3, 'max_f': 4, 'min_m': 3, 'max_m': 4},
        'potential_loyalists': {'min_r': 1, 'max_r': 2, 'min_f': 1, 'max_f': 3, 'min_m': 1, 'max_m': 3},
        'new_customers': {'min_r': 4, 'max_r': 4, 'min_f': 1, 'max_f': 2, 'min_m': 1, 'max_m': 2},
        'promising': {'min_r': 3, 'max_r': 4, 'min_f': 1, 'max_f': 2, 'min_m': 1, 'max_m': 2},
        'need_attention': {'min_r': 2, 'max_r': 3, 'min_f': 2, 'max_f': 3, 'min_m': 2, 'max_m': 3},
        'about_to_sleep': {'min_r': 3, 'max_r': 4, 'min_f': 1, 'max_f': 2, 'min_m': 1, 'max_m': 2},
        'at_risk': {'min_r': 4, 'max_r': 4, 'min_f': 2, 'max_f': 4, 'min_m': 2, 'max_m': 4},
        'cant_lose': {'min_r': 4, 'max_r': 4, 'min_f': 3, 'max_f': 4, 'min_m': 3, 'max_m': 4},
        'hibernating': {'min_r': 4, 'max_r': 4, 'min_f': 1, 'max_f': 2, 'min_m': 1, 'max_m': 2},
    }

    segment_names = {
        'champions': 'Чемпионы',
        'loyal_customers': 'Лояльные',
        'potential_loyalists': 'Потенциальные лояльные',
        'new_customers': 'Новые',
        'promising': 'Перспективные',
        'need_attention': 'Требуют внимания',
        'about_to_sleep': 'Засыпающие',
        'at_risk': 'В зоне риска',
        'cant_lose': 'Нельзя потерять',
        'hibernating': 'Спящие',
    }

    # Рассчитываем RFM баллы для каждого клиента
    rfm_segments = []
    segment_distribution = defaultdict(int)

    for customer_id, data in customers_data.items():
        # Рассчитываем баллы (от 1 до 4, где 1 лучший)
        r_score = 1
        f_score = 1
        m_score = 1

        # Recency score (чем меньше дней, тем лучше)
        if data['recency'] <= recency_quantiles[0]:
            r_score = 4
        elif data['recency'] <= recency_quantiles[1]:
            r_score = 3
        elif data['recency'] <= recency_quantiles[2]:
            r_score = 2

        # Frequency score (чем больше заказов, тем лучше)
        if data['frequency'] >= frequency_quantiles[2]:
            f_score = 4
        elif data['frequency'] >= frequency_quantiles[1]:
            f_score = 3
        elif data['frequency'] >= frequency_quantiles[0]:
            f_score = 2

        # Monetary score (чем больше сумма, тем лучше)
        if data['monetary'] >= monetary_quantiles[2]:
            m_score = 4
        elif data['monetary'] >= monetary_quantiles[1]:
            m_score = 3
        elif data['monetary'] >= monetary_quantiles[0]:
            m_score = 2

        # Определяем сегмент
        segment = 'hibernating'  # по умолчанию
        for seg_name, seg_criteria in segments.items():
            if (seg_criteria['min_r'] <= r_score <= seg_criteria['max_r'] and
                    seg_criteria['min_f'] <= f_score <= seg_criteria['max_f'] and
                    seg_criteria['min_m'] <= m_score <= seg_criteria['max_m']):
                segment = seg_name
                break

        segment_distribution[segment] += 1

        rfm_segments.append({
            'customer_id': customer_id,
            'r_score': r_score,
            'f_score': f_score,
            'm_score': m_score,
            'rfm_score': f"{r_score}{f_score}{m_score}",
            'segment': segment,
            'segment_name': segment_names.get(segment, segment),
            'recency': data['recency'],
            'frequency': data['frequency'],
            'monetary': data['monetary'],
            'avg_order_value': data['avg_order_value'],
            'total_items': data['total_items'],
            'last_order_date': data['last_order_date'],
            'first_order_date': data['first_order_date'],
            'order_count': len(data['order_dates'])
        })

    # Сортируем по RFM score
    rfm_segments.sort(key=lambda x: (-x['r_score'], -x['f_score'], -x['m_score']))

    # Преобразуем распределение сегментов
    segment_stats = []
    total_customers = len(rfm_segments)
    for segment, count in segment_distribution.items():
        segment_stats.append({
            'segment': segment,
            'name': segment_names.get(segment, segment),
            'count': count,
            'percentage': round(count / total_customers * 100, 1) if total_customers > 0 else 0
        })

    segment_stats.sort(key=lambda x: x['count'], reverse=True)

    return rfm_segments, segment_stats


def perform_abc_analysis(products_data, total_revenue):
    """Выполняет ABC анализ товаров"""
    if not products_data:
        return [], []

    # Сортируем товары по выручке
    sorted_products = sorted(
        products_data,
        key=lambda x: x['revenue'],
        reverse=True
    )

    # Рассчитываем кумулятивную сумму
    cumulative_revenue = 0
    for i, product in enumerate(sorted_products):
        cumulative_revenue += product['revenue']
        product['cumulative_revenue'] = cumulative_revenue
        product['cumulative_percentage'] = (cumulative_revenue / total_revenue * 100) if total_revenue > 0 else 0

        # Определяем ABC категорию
        if product['cumulative_percentage'] <= 80:
            product['abc_category'] = 'A'
            product['abc_color'] = '#10B981'  # зеленый
        elif product['cumulative_percentage'] <= 95:
            product['abc_category'] = 'B'
            product['abc_color'] = '#F59E0B'  # желтый
        else:
            product['abc_category'] = 'C'
            product['abc_color'] = '#EF4444'  # красный

    # ABC статистика
    abc_stats = []
    for category in ['A', 'B', 'C']:
        category_products = [p for p in sorted_products if p['abc_category'] == category]
        if category_products:
            abc_stats.append({
                'category': category,
                'count': len(category_products),
                'revenue': sum(p['revenue'] for p in category_products),
                'revenue_percentage': round(sum(p['revenue'] for p in category_products) / total_revenue * 100,
                                            1) if total_revenue > 0 else 0,
                'quantity': sum(p['quantity'] for p in category_products),
                'color': category_products[0]['abc_color']
            })

    return sorted_products, abc_stats


@dashboard_bp.route("/dashboard/analytics-data", methods=["POST"])
@login_required
def analytics_data():
    data = request.json
    start_date_str = data.get('start_date')
    end_date_str = data.get('end_date')

    # Парсим даты
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1)  # Включаем весь день

    # Получаем заказы за период
    orders = list(orders_col.find({
        "created_at": {
            "$gte": start_date,
            "$lt": end_date
        }
    }))

    # Основные метрики
    total_orders = len(orders)
    total_revenue = sum(order.get('total', 0) for order in orders)
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0

    # Анализ товаров
    items_sold = 0
    product_stats = defaultdict(lambda: {
        'quantity': 0,
        'revenue': 0,
        'name': '',
        'category': ''
    })

    # Получаем информацию о товарах из меню
    menu = db.menu.find_one({"_id": "menu"})
    menu_items = menu.get('items', {}) if menu else {}

    # Агрегируем данные по товарам
    for order in orders:
        for item in order.get('items', []):
            items_sold += 1
            item_id = item.get('item_id')
            quantity = item.get('quantity', 1)
            price = item.get('price', 0)

            if item_id in product_stats:
                product_stats[item_id]['quantity'] += quantity
                product_stats[item_id]['revenue'] += price * quantity
            else:
                # Получаем информацию о товаре из меню
                menu_item = menu_items.get(item_id, {})
                product_stats[item_id] = {
                    'quantity': quantity,
                    'revenue': price * quantity,
                    'name': menu_item.get('name', f'Товар {item_id}'),
                    'category': menu_item.get('category', 'Без категории')
                }

    # Подготавливаем данные для таблицы товаров
    sorted_products = sorted(
        product_stats.values(),
        key=lambda x: x['revenue'],
        reverse=True
    )

    # Вычисляем долю в выручке
    for product in sorted_products:
        product['revenue_share'] = round((product['revenue'] / total_revenue * 100) if total_revenue > 0 else 0, 1)
        product['avg_price'] = round(product['revenue'] / product['quantity'] if product['quantity'] > 0 else 0, 2)

    # Анализ по дням
    daily_stats = defaultdict(float)
    hourly_stats = [0] * 24
    weekday_stats = [0] * 7
    category_stats = defaultdict(float)

    # Собираем данные по клиентам для RFM анализа
    customer_orders = defaultdict(list)
    for order in orders:
        user_id = order.get('user_id')
        if user_id:
            customer_orders[user_id].append(order)

        # По датам
        date_key = order['created_at'].strftime("%Y-%m-%d")
        daily_stats[date_key] += order.get('total', 0)

        # По часам
        hour = order['created_at'].hour
        hourly_stats[hour] += order.get('total', 0)

        # По дням недели (0 - понедельник, 6 - воскресенье)
        weekday = order['created_at'].weekday()
        weekday_stats[weekday] += 1

        # По категориям
        for item in order.get('items', []):
            item_id = item.get('item_id')
            menu_item = menu_items.get(item_id, {})
            category = menu_item.get('category', 'Без категории')
            category_stats[category] += item.get('price', 0) * item.get('quantity', 1)

    # RFM Анализ
    rfm_customers_data = {}
    analysis_date = end_date  # Дата для расчета Recency

    for customer_id, customer_order_list in customer_orders.items():
        metrics = calculate_rfm_metrics(customer_order_list, analysis_date)
        if metrics:
            rfm_customers_data[customer_id] = metrics

    rfm_segments, segment_stats = segment_rfm_customers(rfm_customers_data)

    # ABC Анализ товаров
    abc_products, abc_stats = perform_abc_analysis(sorted_products, total_revenue)

    # Подготавливаем данные для графиков
    dates = sorted(daily_stats.keys())
    revenue_by_date = [daily_stats[date] for date in dates]

    # Топ-10 товаров
    top_products = sorted_products[:10]

    # Подготавливаем ответ
    response = {
        'metrics': {
            'total_orders': total_orders,
            'revenue': round(total_revenue, 2),
            'avg_order': round(avg_order_value, 2),
            'items_sold': items_sold,
            'unique_customers': len(customer_orders),
            'avg_orders_per_customer': round(total_orders / len(customer_orders), 1) if customer_orders else 0
        },
        'products': sorted_products,
        'charts': {
            'revenue': {
                'dates': dates,
                'values': revenue_by_date
            },
            'categories': {
                'names': list(category_stats.keys()),
                'values': list(category_stats.values())
            },
            'top_products': {
                'names': [p['name'] for p in top_products],
                'quantity': [p['quantity'] for p in top_products],
                'revenue': [p['revenue'] for p in top_products]
            },
            'hourly': hourly_stats,
            'weekday': weekday_stats
        },
        'rfm_analysis': {
            'customers': rfm_segments[:100],  # Ограничиваем для производительности
            'segment_distribution': segment_stats,
            'total_customers': len(rfm_segments),
            'avg_recency': round(np.mean([c['recency'] for c in rfm_segments]), 1) if rfm_segments else 0,
            'avg_frequency': round(np.mean([c['frequency'] for c in rfm_segments]), 1) if rfm_segments else 0,
            'avg_monetary': round(np.mean([c['monetary'] for c in rfm_segments]), 1) if rfm_segments else 0,
            'top_customers': sorted(rfm_segments, key=lambda x: x['monetary'], reverse=True)[:10]
        },
        'abc_analysis': {
            'products': abc_products,
            'stats': abc_stats,
            'total_products': len(abc_products),
            'category_a_count': sum(1 for p in abc_products if p['abc_category'] == 'A'),
            'category_b_count': sum(1 for p in abc_products if p['abc_category'] == 'B'),
            'category_c_count': sum(1 for p in abc_products if p['abc_category'] == 'C')
        }
    }

    return jsonify(response)


@dashboard_bp.route("/dashboard/rfm-details")
@login_required
def rfm_details():
    """Детализированный RFM анализ"""
    try:
        # Получаем все заказы за последний год
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)

        orders = list(orders_col.find({
            "created_at": {
                "$gte": start_date,
                "$lt": end_date
            }
        }))

        # Собираем данные по клиентам
        customer_orders = defaultdict(list)
        for order in orders:
            user_id = order.get('user_id')
            if user_id:
                customer_orders[user_id].append(order)

        # RFM анализ
        rfm_customers_data = {}
        for customer_id, customer_order_list in customer_orders.items():
            metrics = calculate_rfm_metrics(customer_order_list, end_date)
            if metrics:
                rfm_customers_data[customer_id] = metrics

        rfm_segments, segment_stats = segment_rfm_customers(rfm_customers_data)

        # Собираем детальную статистику
        rfm_details = {
            'total_customers': len(rfm_segments),
            'segments': segment_stats,
            'top_customers': sorted(rfm_segments, key=lambda x: x['monetary'], reverse=True)[:50],
            'recent_customers': sorted(rfm_segments, key=lambda x: x['recency'])[:50],
            'frequent_customers': sorted(rfm_segments, key=lambda x: x['frequency'], reverse=True)[:50],
            'metrics': {
                'avg_recency': round(np.mean([c['recency'] for c in rfm_segments]), 1) if rfm_segments else 0,
                'avg_frequency': round(np.mean([c['frequency'] for c in rfm_segments]), 1) if rfm_segments else 0,
                'avg_monetary': round(np.mean([c['monetary'] for c in rfm_segments]), 1) if rfm_segments else 0,
                'total_monetary': sum(c['monetary'] for c in rfm_segments),
                'median_recency': round(np.median([c['recency'] for c in rfm_segments]), 1) if rfm_segments else 0,
                'median_frequency': round(np.median([c['frequency'] for c in rfm_segments]), 1) if rfm_segments else 0,
                'median_monetary': round(np.median([c['monetary'] for c in rfm_segments]), 1) if rfm_segments else 0,
            }
        }

        return jsonify(rfm_details)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@dashboard_bp.route("/dashboard/abc-details")
@login_required
def abc_details():
    """Детализированный ABC анализ"""
    try:
        # Получаем все заказы за последний год
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)

        orders = list(orders_col.find({
            "created_at": {
                "$gte": start_date,
                "$lt": end_date
            }
        }))

        # Получаем информацию о товарах из меню
        menu = db.menu.find_one({"_id": "menu"})
        menu_items = menu.get('items', {}) if menu else {}

        # Анализ товаров
        product_stats = defaultdict(lambda: {
            'quantity': 0,
            'revenue': 0,
            'name': '',
            'category': ''
        })

        total_revenue = 0
        for order in orders:
            total_revenue += order.get('total', 0)
            for item in order.get('items', []):
                item_id = item.get('item_id')
                quantity = item.get('quantity', 1)
                price = item.get('price', 0)

                if item_id in product_stats:
                    product_stats[item_id]['quantity'] += quantity
                    product_stats[item_id]['revenue'] += price * quantity
                else:
                    menu_item = menu_items.get(item_id, {})
                    product_stats[item_id] = {
                        'quantity': quantity,
                        'revenue': price * quantity,
                        'name': menu_item.get('name', f'Товар {item_id}'),
                        'category': menu_item.get('category', 'Без категории')
                    }

        # Подготавливаем данные для ABC анализа
        products_list = list(product_stats.values())

        # Добавляем среднюю цену
        for product in products_list:
            product['avg_price'] = round(product['revenue'] / product['quantity'] if product['quantity'] > 0 else 0, 2)

        # Выполняем ABC анализ
        abc_products, abc_stats = perform_abc_analysis(products_list, total_revenue)

        # Детальная статистика по категориям
        category_analysis = defaultdict(lambda: {
            'total_revenue': 0,
            'total_quantity': 0,
            'products': [],
            'abc_distribution': {'A': 0, 'B': 0, 'C': 0}
        })

        for product in abc_products:
            category = product['category']
            category_analysis[category]['total_revenue'] += product['revenue']
            category_analysis[category]['total_quantity'] += product['quantity']
            category_analysis[category]['products'].append(product)
            category_analysis[category]['abc_distribution'][product['abc_category']] += 1

        # Преобразуем в список
        category_stats = []
        for category, data in category_analysis.items():
            category_stats.append({
                'category': category,
                'total_revenue': data['total_revenue'],
                'total_quantity': data['total_quantity'],
                'product_count': len(data['products']),
                'abc_distribution': data['abc_distribution'],
                'top_products': sorted(data['products'], key=lambda x: x['revenue'], reverse=True)[:5]
            })

        category_stats.sort(key=lambda x: x['total_revenue'], reverse=True)

        abc_details = {
            'total_products': len(abc_products),
            'total_revenue': total_revenue,
            'abc_stats': abc_stats,
            'category_analysis': category_stats,
            'top_products_a': [p for p in abc_products if p['abc_category'] == 'A'][:20],
            'top_products_b': [p for p in abc_products if p['abc_category'] == 'B'][:20],
            'top_products_c': [p for p in abc_products if p['abc_category'] == 'C'][:20],
            'metrics': {
                'avg_revenue_per_product': round(total_revenue / len(abc_products), 2) if abc_products else 0,
                'avg_quantity_per_product': round(sum(p['quantity'] for p in abc_products) / len(abc_products),
                                                  1) if abc_products else 0,
                'revenue_concentration': round(abc_stats[0]['revenue_percentage'] if abc_stats else 0, 1),
                # % товаров категории A
                'pareto_80_20': len([p for p in abc_products if p['cumulative_percentage'] <= 80])
                # Кол-во товаров в 80% выручки
            }
        }

        return jsonify(abc_details)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@dashboard_bp.route("/dashboard/connect_bot", methods=["POST"])
@login_required
def connect_bot():
    data = request.json
    token = data.get("token")

    if not token:
        return jsonify({"ok": False, "error": "Токен не предоставлен"}), 400

    try:
        # Используем bot_manager для запуска
        success = bot_manager.start_bot(token)
        if success:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "Не удалось запустить бота"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@dashboard_bp.route("/dashboard/stop_bot", methods=["POST"])
@login_required
def stop_bot_route():
    try:
        success = bot_manager.stop_bot()
        if success:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": "Не удалось остановить бота"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@dashboard_bp.route("/dashboard/bot_status")
@login_required
def bot_status():
    try:
        status = bot_manager.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})