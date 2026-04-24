import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';
import http from 'k6/http';

const orderResponseTime = new Trend('order_response_time', true);
const browseResponseTime = new Trend('browse_response_time', true);
const cartResponseTime = new Trend('cart_response_time', true);
const errorRate = new Rate('error_rate');
const totalOrders = new Counter('total_orders');

const RUN_NUMBER = __ENV.RUN_NUMBER || '1';
const SCENARIO_NAME = __ENV.SCENARIO_NAME || 'baseline';

const BASELINE_CONFIG = {
    concurrentUsers: 100,
    workloadMix: 'Balanced',
    serviceReplicas: 3,
    databaseSize: 'Medium',
    vmCpuCores: 2,
    vmMemoryGB: 4
};

const WORKLOADS = {
    'Browse-Heavy': { browse: 0.90, cart: 0.05, order: 0.05 },
    'Balanced': { browse: 0.70, cart: 0.20, order: 0.10 },
    'Checkout-Heavy': { browse: 0.50, cart: 0.20, order: 0.30 }
};

const currentWorkload = WORKLOADS[BASELINE_CONFIG.workloadMix];
const BASE = 'http://localhost:8080/tools.descartes.teastore.webui';

const PRODUCTS_PER_CATEGORY = 2000;
const NUM_CATEGORIES = 5;

export let options = {
    vus: BASELINE_CONFIG.concurrentUsers,
    duration: '5m',
    tags: {
        run: RUN_NUMBER,
        scenario: SCENARIO_NAME,
    },
    thresholds: {
        'order_response_time': ['p(95)<5000', 'p(99)<10000'],
        'error_rate': ['rate<0.01'],
    },
};

function thinkTime() {
    sleep(Math.random() * 2 + 3);
}

function randomProductId() {
    return Math.floor(Math.random() * (PRODUCTS_PER_CATEGORY * NUM_CATEGORIES)) + 1;
}

function randomCategory() {
    return Math.floor(Math.random() * NUM_CATEGORIES) + 1;
}

function randomPage() {
    return Math.floor(Math.random() * 10) + 1;
}

export default function () {
    let random = Math.random();

    try {
        if (random < currentWorkload.browse) {
            group('Browse Products', () => {
                let start = Date.now();

                let homeRes = http.get(`${BASE}/`);
                check(homeRes, { 'Homepage OK': (r) => r.status === 200 });

                let categoryRes = http.get(`${BASE}/category?page=${randomPage()}&category=${randomCategory()}`);
                check(categoryRes, { 'Category OK': (r) => r.status === 200 });

                let productRes = http.get(`${BASE}/product?id=${randomProductId()}`);
                check(productRes, { 'Product OK': (r) => r.status === 200 });

                browseResponseTime.add(Date.now() - start);
                thinkTime();
            });
        } else if (random < (currentWorkload.browse + currentWorkload.cart)) {
            group('Add to Cart', () => {
                let start = Date.now();

                let loginRes = http.post(`${BASE}/loginAction`, {
                    referer: `${BASE}/login`,
                    username: 'user2',
                    password: 'password',
                });
                check(loginRes, { 'Login OK': (r) => r.status === 200 });

                let pid = randomProductId();
                let cartRes = http.post(`${BASE}/cartAction`, {
                    addToCart: '',
                    productid: `${pid}`,
                });

                check(cartRes, { 'Cart add OK': (r) => r.status === 200 });

                cartResponseTime.add(Date.now() - start);
                errorRate.add((loginRes.status !== 200 || cartRes.status !== 200) ? 1 : 0);
                thinkTime();
            });
        } else {
            group('Place Order', () => {
                let start = Date.now();

                let loginRes = http.post(`${BASE}/loginAction`, {
                    referer: `${BASE}/login`,
                    username: 'user2',
                    password: 'password',
                });
                check(loginRes, { 'Login OK': (r) => r.status === 200 });

                let pid = randomProductId();
                let addRes = http.post(`${BASE}/cartAction`, {
                    addToCart: '',
                    productid: `${pid}`,
                });
                check(addRes, { 'Add to cart OK': (r) => r.status === 200 });

                let orderRes = http.post(`${BASE}/cartAction`, {
                    proceedToCheckout: '',
                });
                check(orderRes, { 'Order OK': (r) => r.status === 200 });

                let duration = Date.now() - start;
                orderResponseTime.add(duration);

                if (loginRes.status === 200 && addRes.status === 200 && orderRes.status === 200) {
                    totalOrders.add(1);
                    errorRate.add(0);
                } else {
                    errorRate.add(1);
                }

                thinkTime();
            });
        }
    } catch (error) {
        console.error(`Error in VU ${__VU}: ${error}`);
        errorRate.add(1);
    }
}

export function handleSummary(data) {
    const filename = `${SCENARIO_NAME}_results_run${RUN_NUMBER}.json`;
    return {
        [filename]: JSON.stringify(data, null, 2),
    };
}