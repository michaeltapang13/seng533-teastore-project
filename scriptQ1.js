// Q1: VUs increased in [100, 900] & decreased in [900, 100]. Frequency of 100. Total time 4m45s.

import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';
import http from 'k6/http';




const orderResponseTime = new Trend('order_response_time', true);
const browseResponseTime = new Trend('browse_response_time', true);
const cartResponseTime = new Trend('cart_response_time', true);
const errorRate = new Rate('error_rate');
const totalOrders = new Counter('total_orders');

const BASELINE_CONFIG = {
    concurrentUsers: 10,
    workloadMix: 'Balanced',
    serviceReplicas: 1,
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
const BASE = 'http://10.1.3.121:8080/tools.descartes.teastore.webui';

export let options = {
    vus: BASELINE_CONFIG.concurrentUsers,
    // duration: '5m',
    thresholds: {
        'order_response_time': ['p(95)<5000', 'p(99)<10000'],
        'error_rate': ['rate<0.01'],
    },
        stages: [
        { duration: '30s', target: 100 },
        { duration: '15s', target: 200 },
        { duration: '15s', target: 300 },
        { duration: '15s', target: 400 },
        { duration: '15s', target: 500 },
        { duration: '15s', target: 600 },
        { duration: '15s', target: 700 },
        { duration: '15s', target: 800 },
        { duration: '15s', target: 900 },
        { duration: '15s', target: 900 },
        { duration: '15s', target: 800 },
        { duration: '15s', target: 700 },
        { duration: '15s', target: 600 },
        { duration: '15s', target: 500 },
        { duration: '15s', target: 400 },
        { duration: '15s', target: 300 },
        { duration: '15s', target: 200 },
        { duration: '15s', target: 100 },
        { duration: '15s', target: 0 },
    ],
    http: {
        timeout: '2m',   // Total Time out
    },
};

function thinkTime() {
    sleep(Math.random() * 2 + 1);
}


export default function () {
    let jar = http.cookieJar();
    let loginRes = http.post(`${BASE}/loginAction`, {
        referer: `${BASE}/login`,
        username: 'user2',
        password: 'password',
    });

    if (loginRes.status !== 200) {
        errorRate.add(1);
        return;
    }
    let random = Math.random();

    try {
        if (random < currentWorkload.browse) {
            group('Browse Products', () => {
                let start = Date.now();

                let homeRes = http.get(`${BASE}/`);
                check(homeRes, { 'Homepage OK': (r) => r.status === 200 });

                let categoryRes = http.get(`${BASE}/category?page=1&category=2`);
                check(categoryRes, { 'Category OK': (r) => r.status === 200 });

                let productRes = http.get(`${BASE}/product?id=7`);
                check(productRes, { 'Product OK': (r) => r.status === 200 });

                browseResponseTime.add(Date.now() - start);
                thinkTime();
            });
        }
        else if (random < (currentWorkload.browse + currentWorkload.cart)) {
            group('Add to Cart', () => {
                let start = Date.now();

                http.post(`${BASE}/loginAction`, {
                    referer: `${BASE}/login`,
                    username: 'user2',
                    password: 'password',
                });

                let cartRes = http.post(`${BASE}/cartAction`, {
                    addToCart: '',
                    productid: '7',
                });

                check(cartRes, { 'Cart add OK': (r) => r.status === 200 });
                cartResponseTime.add(Date.now() - start);
                errorRate.add(cartRes.status !== 200 ? 1 : 0);
                thinkTime();
            });
        }
        else {
            group('Place Order', () => {
                let start = Date.now();

                let loginRes = http.post(`${BASE}/loginAction`, {
                    referer: `${BASE}/login`,
                    username: 'user2',
                    password: 'password',
                });
                check(loginRes, { 'Login OK': (r) => r.status === 200 });

                http.post(`${BASE}/cartAction`, {
                    addToCart: '',
                    productid: '7',
                });

                let orderRes = http.post(`${BASE}/cartAction`, {
                    proceedToCheckout: '',
                });

                let duration = Date.now() - start;
                orderResponseTime.add(duration);

                if (orderRes.status === 200) {
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
    console.log('\n');
    console.log('=== BASELINE TEST COMPLETE ===');
    console.log(`Concurrent Users: ${BASELINE_CONFIG.concurrentUsers}`);
    console.log(`Workload Mix: ${BASELINE_CONFIG.workloadMix}`);
    console.log(`Service Replicas: ${BASELINE_CONFIG.serviceReplicas}`);
    console.log(`Database Size: ${BASELINE_CONFIG.databaseSize}`);
    console.log(`VM Resources: ${BASELINE_CONFIG.vmCpuCores} CPU, ${BASELINE_CONFIG.vmMemoryGB}GB RAM`);
    console.log(`Total Orders: ${data.metrics.total_orders.values.count}`);
    console.log(`Throughput: ${(data.metrics.total_orders.values.count / 300).toFixed(2)} orders/sec`);
    console.log(`Avg Response Time: ${data.metrics.order_response_time.values.avg.toFixed(2)}ms`);
    console.log(`P95 Response Time: ${data.metrics.order_response_time.values['p(95)'].toFixed(2)}ms`);
    console.log(`P99 Response Time: ${data.metrics.order_response_time.values['p(99)'].toFixed(2)}ms`);
    console.log(`Error Rate: ${(data.metrics.error_rate.values.rate * 100).toFixed(2)}%`);

    return {
        'baseline_results.json': JSON.stringify(data, null, 2),
    };
}
