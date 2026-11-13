# Cấu hình tài khoản Firebase

## Bổ sung cho Your contribution

Publish lại file `firestore.rules` ở thư mục gốc để cho phép chủ tài khoản đọc/ghi metadata tại `users/{uid}/contributions/{gifId}`. Rules mới giữ nguyên quyền riêng tư của favorites và chỉ mở collection contribution cho đúng UID sở hữu; không mở toàn bộ database.

Trên Firebase Console, vào project `gift-main` → Firestore Database → Rules, thay nội dung bằng file `firestore.rules` rồi Publish. Sau đó tải lại website và mở **Your contribution** để đồng bộ metadata. Lỗi đồng bộ chỉ được ghi trong **F12 → Console** với tiền tố `[gifly:cloud-sync]`; giao diện không hiện banner hoặc toast. Sau khi sửa cấu hình, tải lại trang để thử đồng bộ lại.

GIF và tác vụ được lưu bền vững trên server theo Firebase UID. Metadata được đồng bộ vào collection của tài khoản; chỉ GIF đặt public mới được đưa vào tìm kiếm chung. Xem `web/CONTRIBUTIONS.md` để biết cấu hình lưu trữ và giới hạn.

## Thiết lập tài khoản hiện có

Ứng dụng dùng Firebase Authentication cho username và password, Cloud Firestore cho GIF yêu thích. Khách vẫn tìm kiếm khi Firebase chưa được cấu hình.

1. Tạo Firebase project và đăng ký một Web app trong Project settings.
2. Chép apiKey, authDomain, projectId và appId vào web/firebase-config.js. Đây là cấu hình công khai. Giữ service account ngoài frontend.
3. Giữ AUTH_USERNAME_DOMAIN ổn định. Mặc định users.gifly.invalid, chỉ dùng cho định danh nội bộ. Username được trim và viết thường. Không gửi thư đến email nội bộ.
4. Trong Authentication → Sign-in method, bật Email/Password. Trong Settings → Password policy, chọn Require và độ dài tối thiểu 8. Thêm hostname triển khai vào Authorized domains, bao gồm localhost khi chạy cục bộ.
5. Tạo Cloud Firestore ở chế độ production.
6. Dùng Firebase CLI chạy lệnh firebase deploy --only firestore:rules --project YOUR_FIREBASE_PROJECT_ID từ thư mục gốc. Hoặc dán firestore.rules vào tab Rules và Publish.

Chạy lại start-web.ps1 để phục vụ module Firebase mới rồi tải lại trang. Không cần Firebase Admin SDK.

Đăng ký thành công sẽ đăng nhập ngay. Nếu cập nhật displayName lỗi, tài khoản vẫn tồn tại. Ứng dụng ghi thông báo vào Console và thử cập nhật tên ở lần đăng nhập sau.

Tài khoản SQLite và yêu thích cũ không tự chuyển sang Firebase. File cũ còn trong web/.local để chủ dự án lập kế hoạch chuyển dữ liệu.

## Kiểm chứng khi có cấu hình thật

- Đăng ký user_test, xác nhận form đóng và username xuất hiện.
- Đăng nhập bằng USER_TEST và user_test để xác nhận cùng định danh.
- Lưu GIF, tải lại trang, đăng xuất và đăng nhập lại, thử trên thiết bị khác.
- Đổi tài khoản A sang B và xác nhận danh sách A đã được dọn.
- Trong Rules Playground hoặc Emulator, dùng UID A đọc/ghi users/B/favorites/test. Cả hai phải bị từ chối. Lặp lại với khách.
- Thử ghi trường khác gifId hoặc gifId khác document ID. Rules phải từ chối.
- Ngắt mạng trước và trong khi lưu. Không được báo thành công giả. Nút phải cho phép thử lại.
- Ngắt mạng khi đăng nhập/đăng ký và kiểm tra form được mở khóa.
- Chặn SDK và kiểm tra khách vẫn tìm kiếm được.

Đã kiểm chứng trên project Firebase thật ngày 2026-11-12.

- Tạo hai tài khoản tạm, cập nhật displayName và đăng nhập thành công.
- Chủ tài khoản lưu, đọc và mở lại yêu thích sau một lần đăng nhập mới.
- Rules chặn khách và tài khoản B đọc, sửa hoặc xóa yêu thích của A, đồng thời từ chối gifId không khớp.
- Trên giao diện đã kiểm tra đăng ký tự đăng nhập, username viết hoa/thường, lưu từ trạng thái khách, giữ phiên sau tải lại, Saved và đăng xuất.
- Đã sửa hai lỗi điều hướng khi đăng xuất từ Saved và khi đăng nhập để mở Saved.
- Đã dọn toàn bộ tài khoản và dữ liệu dùng riêng cho các lượt kiểm thử trên.

Chưa kiểm chứng đồng bộ giữa hai thiết bị vật lý hoặc mất mạng thực tế.

Tài liệu chính thức

- https://firebase.google.com/docs/auth/web/password-auth
- https://firebase.google.com/docs/auth/web/auth-state-persistence
- https://firebase.google.com/docs/firestore/security/rules-conditions
